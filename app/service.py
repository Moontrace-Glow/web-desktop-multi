"""服务管理模块：负责 web 服务子进程的启动、健康检查、日志记录与进程树清理。"""

import logging
import os
import re
import subprocess
import urllib.error
import urllib.request

from app.paths import expand_home_path, get_app_dir


class WebServiceError(Exception):
    """服务启动失败时抛出的业务异常。"""


def _parse_windows_command(command: str) -> list:
    """
    按 Windows 命令行规则（CreateProcess / CommandLineToArgvW）解析启动命令字符串。

    - 空格、制表符分隔参数，双引号包裹的文本视为一个参数（含空格的路径）
    - 引号字符本身剥离，`\\"` 表示字面引号，`\\` 反斜杠保持字面
    - 命令为空或全空白时返回空列表

    Args:
        command: 完整命令行字符串，如 `"D:\\my app\\server.exe" --port 8080`。

    Returns:
        参数列表。
    """
    args = []
    current = ""
    in_quotes = False
    i = 0
    length = len(command)
    while i < length:
        ch = command[i]
        if ch == "\\":
            # 收集连续反斜杠，若后跟引号则按转义规则处理
            j = i
            while j < length and command[j] == "\\":
                j += 1
            count = j - i
            if j < length and command[j] == '"':
                # 偶数个反斜杠：输出一半，引号作为控制字符（进入循环处理）
                # 奇数个反斜杠：输出一半反斜杠 + 一个字面引号
                current += "\\" * (count // 2)
                if count % 2 == 1:
                    current += '"'
                    i = j + 1
                    continue
                i = j
                continue
            current += "\\" * count
            i = j
            continue
        if ch == '"':
            in_quotes = not in_quotes
            i += 1
            continue
        if ch in " \t" and not in_quotes:
            # 引号外的空白为参数分隔符
            if current:
                args.append(current)
                current = ""
            i += 1
            continue
        current += ch
        i += 1
    if current:
        args.append(current)
    return args


class WebServiceManager:
    """管理 web 服务子进程的生命周期。"""

    def __init__(self, config: dict):
        """
        初始化服务管理器。

        Args:
            config: 应用配置字典（web_command、web_url 等）。
        """
        self._config = config
        self._process = None
        self._log_path = None
        self._log_file = None

    def start(self) -> None:
        """
        启动服务子进程，输出重定向到日志文件。

        Raises:
            WebServiceError: 命令为空、命令不存在或启动失败。
        """
        # 配置项 web_command 为完整命令行字符串，按 Windows 规则解析为参数列表
        command = self._config["web_command"]
        cmd = _parse_windows_command(command)
        if not cmd:
            raise WebServiceError(
                "配置项 web_command（服务启动命令）为空，请检查 config.json 配置"
            )

        # 准备日志文件（服务 stdout/stderr 均写入该文件，便于排查启动失败原因）。
        # 每次服务启动都重新打开并以「写入（截断）」模式清空历史日志，避免文件无限累积；
        # 同时保证从日志中提取访问地址（find_log_url）只会匹配到本次启动的地址，
        # 而不会命中上一轮已失效的地址（如旧的 dsh web 一次性 token）。
        # log_dir 默认 ~/.WebDesktop/log（配置加载时已转为绝对路径），此处兜底展开 ~ 并回退
        log_dir = self._config.get("log_dir") or os.path.join(get_app_dir(), "log")
        log_dir = expand_home_path(log_dir)
        os.makedirs(log_dir, exist_ok=True)
        self._log_path = os.path.join(log_dir, "web_service.log")
        self._log_file = open(self._log_path, "wb")

        # Windows 下创建独立进程组，配合 taskkill /T 可清理整个进程树；
        # 默认隐藏子进程的控制台窗口（show_console 为 true 时显示，便于调试）
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if not self._config.get("show_console", False):
            creation_flags |= subprocess.CREATE_NO_WINDOW

        # 工作目录默认 ~/.WebDesktop/working；目录不存在时自动创建，避免启动失败
        working_dir = self._config.get("working_dir") or None
        if working_dir:
            working_dir = expand_home_path(working_dir)
            os.makedirs(working_dir, exist_ok=True)
        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=working_dir,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
        except FileNotFoundError as exc:
            self._close_log()
            raise WebServiceError(
                f"无法启动命令「{command}」：命令不存在。请检查 web_command 配置，"
                "建议填写可执行文件的完整路径"
            ) from exc
        except OSError as exc:
            self._close_log()
            raise WebServiceError(f"启动服务失败：{exc}") from exc
        logging.info("服务进程已启动，PID=%s，命令=%s", self._process.pid, " ".join(cmd))

    def is_running(self) -> bool:
        """
        判断服务进程是否仍在运行。

        Returns:
            True 表示进程存活。
        """
        return self._process is not None and self._process.poll() is None

    def exit_code(self):
        """
        获取服务进程退出码（进程仍在运行时返回 None）。

        Returns:
            退出码或 None。
        """
        if self._process is None:
            return None
        return self._process.poll()

    def is_ready(self) -> bool:
        """
        健康检查：请求 web_url，能收到任何 HTTP 响应即视为服务就绪。

        Returns:
            True 表示服务已就绪。
        """
        url = self._config["web_url"]
        timeout = self._config.get("check_timeout", 2)
        try:
            request = urllib.request.Request(
                url, method="GET", headers={"User-Agent": "WebDesktop/1.0"}
            )
            with urllib.request.urlopen(request, timeout=timeout):
                return True
        except urllib.error.HTTPError:
            # HTTPError 表示服务已返回响应（如 404、500），说明服务本身已启动
            return True
        except Exception:
            # 连接被拒绝、超时等异常说明服务尚未就绪
            return False

    def read_log_tail(self, max_lines: int = 30) -> str:
        """
        读取服务日志末尾若干行，用于错误页展示排查信息。

        Args:
            max_lines: 最多读取的行数。

        Returns:
            日志末尾文本；无日志时返回空字符串。
        """
        if not self._log_path or not os.path.exists(self._log_path):
            return ""
        try:
            # 只读取文件末尾最多 64KB，避免大日志拖慢界面
            with open(self._log_path, "rb") as file:
                file.seek(0, os.SEEK_END)
                file_size = file.tell()
                file.seek(max(0, file_size - 64 * 1024))
                data = file.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            return "\n".join(lines[-max_lines:])
        except OSError as exc:
            logging.warning("读取服务日志失败：%s", exc)
            return ""

    def find_log_url(self, pattern: str, scan_window_bytes: int = 256 * 1024):
        """
        从服务日志中按正则提取一个访问地址（用于 url_source 为 log 时的跳转）。

        取第一个匹配结果：若正则含捕获组，则返回第 1 个捕获组的内容（无匹配组时返回 0 组，
        即整段匹配）；找不到匹配、正则非法或读取失败时返回 None。读取最近的一段日志
        （scan_window_bytes 字节），避免大日志拖慢界面。

        Args:
            pattern: 用于匹配访问地址的正则表达式。
            scan_window_bytes: 最多扫描的日志末尾字节数。

        Returns:
            提取出的访问地址字符串；未找到或异常时返回 None。
        """
        if not isinstance(pattern, str) or not pattern.strip():
            return None
        if not self._log_path or not os.path.exists(self._log_path):
            return None
        try:
            with open(self._log_path, "rb") as file:
                file.seek(0, os.SEEK_END)
                file_size = file.tell()
                file.seek(max(0, file_size - scan_window_bytes))
                data = file.read().decode("utf-8", errors="replace")
            try:
                match = re.search(pattern, data)
            except re.error:
                logging.warning("服务日志正则非法，无法提取访问地址：%s", pattern)
                return None
            if match is None:
                return None
            # 优先取第 1 个捕获组；无捕获组时取整段匹配
            value = match.group(1) if match.groups() else match.group(0)
            url = value.strip() if isinstance(value, str) else ""
            return url or None
        except OSError as exc:
            logging.warning("读取服务日志失败：%s", exc)
            return None

    def stop(self, timeout: int = 10) -> None:
        """
        停止服务：使用 taskkill /T 终止整个进程树，并关闭日志文件。

        Args:
            timeout: 等待 taskkill 结束的超时秒数。
        """
        process = self._process
        self._process = None
        if process is not None:
            if self._config.get("kill_on_exit", True):
                self._kill_process_tree(process, timeout)
            else:
                logging.info("kill_on_exit 为 false，服务进程 PID=%s 保持后台运行", process.pid)
        self._close_log()

    def _kill_process_tree(self, process: subprocess.Popen, timeout: int) -> None:
        """
        强制终止指定进程及其全部子进程。

        Args:
            process: 待终止的子进程对象。
            timeout: taskkill 执行超时秒数。
        """
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            logging.info("已结束服务进程树，PID=%s", process.pid)
        except Exception as exc:
            logging.warning("结束服务进程失败：%s", exc)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logging.warning("服务进程未在预期时间内退出")

    def _close_log(self) -> None:
        """关闭日志文件句柄。"""
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
