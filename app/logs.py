"""日志中心模块：集中保存各服务日志的环形缓冲，并向控制器交付「待推送」增量。

职责边界：
- :class:`~app.process.ServiceProcess` 负责把子进程输出落盘（每服务一个日志文件）；
- 本模块只负责内存态：环形缓冲（供前端打开日志面板时补拉历史）+ 待推送队列
  （由控制器定时批量取走并推送给前端），从而做到「实时推送」且不产生
  每行一次 JS 调用的开销。

键（key）为 `项目ID/服务名`，与前端日志面板的分组维度一致。
"""

import threading
from collections import deque

# 每个服务在内存中保留的最大日志行数（前端补拉历史的窗口）
DEFAULT_CAPACITY = 3000

# 单行日志最大字符数，超长行截断，避免前端渲染卡顿
MAX_LINE_LENGTH = 8000


class LogHub:
    """线程安全的日志环形缓冲与增量分发中心。"""

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        """
        初始化日志中心。

        Args:
            capacity: 每个服务保留的最大日志行数。
        """
        self._capacity = max(1, int(capacity))
        self._lock = threading.Lock()
        # key -> 行缓冲（deque 自动丢弃最旧的行）
        self._buffers = {}
        # key -> 累计写入行数（即使被环形缓冲丢弃也持续增长，作为前端的偏移量基准）
        self._totals = {}
        # key -> 尚未推送给前端的行
        self._pending = {}

    @staticmethod
    def make_key(project_id: str, service_name: str) -> str:
        """
        生成日志键。

        Args:
            project_id: 项目标识。
            service_name: 服务名。

        Returns:
            `项目ID/服务名` 形式的键。
        """
        return f"{project_id}/{service_name}"

    def write(self, key: str, line: str) -> None:
        """
        写入一行日志（超长行会被截断）。

        Args:
            key: 日志键（见 make_key）。
            line: 日志文本（不含换行符）。
        """
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + " …（本行已截断）"
        with self._lock:
            buffer = self._buffers.get(key)
            if buffer is None:
                buffer = deque(maxlen=self._capacity)
                self._buffers[key] = buffer
                self._totals[key] = 0
                self._pending[key] = []
            buffer.append(line)
            self._totals[key] += 1
            self._pending[key].append(line)

    def since(self, key: str, offset: int):
        """
        读取指定偏移之后的日志（前端打开日志面板时补拉历史）。

        Args:
            key: 日志键。
            offset: 前端已持有的行数偏移（从 0 开始）。

        Returns:
            {"offset": 最新偏移, "dropped": 被环形缓冲丢弃的行数, "lines": [...]}。
        """
        with self._lock:
            buffer = self._buffers.get(key)
            total = self._totals.get(key, 0)
            if buffer is None:
                return {"offset": total, "dropped": 0, "lines": []}
            start = max(0, min(int(offset or 0), total))
            first_kept = total - len(buffer)
            dropped = max(0, first_kept - start)
            start = max(start, first_kept)
            lines = list(buffer)[start - first_kept:]
            return {"offset": total, "dropped": dropped, "lines": lines}

    def take_pending(self) -> dict:
        """
        取走全部待推送日志（由控制器定时调用后批量推送给前端）。

        返回结构中的 `offset` 是这批日志写入后的累计行数，前端据此判断
        推送是否连续（不连续则重新补拉，避免日志乱序或丢失）。

        Returns:
            {key: {"lines": [行, ...], "offset": 累计行数}}；无新日志时返回空字典。
        """
        with self._lock:
            if not any(self._pending.values()):
                return {}
            result = {}
            for key, lines in self._pending.items():
                if lines:
                    result[key] = {"lines": lines, "offset": self._totals.get(key, 0)}
            self._pending = {key: [] for key in self._pending}
            return result

    def totals(self, key: str) -> int:
        """
        读取某个键的累计写入行数。

        Args:
            key: 日志键。

        Returns:
            累计行数（键不存在时为 0）。
        """
        with self._lock:
            return self._totals.get(key, 0)

    def tail(self, key: str, max_lines: int = 30) -> str:
        """
        读取某个服务的日志末尾若干行（错误提示用）。

        Args:
            key: 日志键。
            max_lines: 最多返回的行数。

        Returns:
            以换行连接的文本；无日志时返回空字符串。
        """
        with self._lock:
            buffer = self._buffers.get(key)
            if not buffer:
                return ""
            lines = list(buffer)[-max(1, int(max_lines)):]
        return "\n".join(lines)

    def clear(self, key: str = "") -> None:
        """
        清空指定键（或全部）的内存日志缓冲（不删除磁盘日志文件）。

        Args:
            key: 日志键；为空表示清空全部。
        """
        with self._lock:
            keys = [key] if key else list(self._buffers)
            for item in keys:
                self._buffers.pop(item, None)
                self._pending.pop(item, None)
                # 保留累计计数，确保前端偏移量基准不回退
                self._totals.setdefault(item, 0)

    def keys(self) -> list:
        """
        列出当前已有日志的键。

        Returns:
            键列表（无序）。
        """
        with self._lock:
            return list(self._buffers)
