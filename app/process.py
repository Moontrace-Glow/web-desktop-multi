"""服务进程模块：封装单个本地服务的启动、健康检查、实时日志与进程树清理。

与项目硬约束保持一致：
- 不使用 `shell=True`，不调用 `.cmd` / `.bat` / `npm run`，一律「可执行文件 + 参数列表」启动；
- 子进程附加 `CREATE_NO_WINDOW`，杜绝控制台窗口弹出；
- 子进程附加 `CREATE_NEW_PROCESS_GROUP`，停止时用 `taskkill /T /F` 清理整棵进程树；
- 服务日志既实时回调（推给前端）也落盘（便于事后排查），两者不互斥。

状态机：`stopped` → `starting` → `running`，异常进入 `error`，停止过程为 `stopping`。
"""

import ctypes
import dataclasses
import locale
import logging
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque

# Windows 进程创建标志（缺失时回退到文档值，保证跨解释器可用）
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

# ---- Windows 作业对象（Job Object）----
# 进程树清理采用三道保险，按顺序执行，直到进程确认退出：
#   1. taskkill /F /T /PID —— 按运行中的进程树逐层终止（常规环境最可靠，能覆盖早于
#      加入作业就派生的孙进程）；
#   2. 作业对象 TerminateJobObject —— 内核对作业成员的终止，不受跨进程权限影响
#      （实测：受限环境下 taskkill 会被「拒绝访问」，作业对象仍可终止）；
#   3. Popen.kill() —— 直接终止子进程句柄，作为最后兜底。
# 已知边界：作业对象只覆盖「加入作业之后」派生的进程；加入前几毫秒内派生的孙进程
# 需依靠第 1 道保险清理。
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

_IS_WINDOWS = hasattr(ctypes, "WinDLL")

if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:
    _kernel32 = None


class _IoCounters(ctypes.Structure):
    """Win32 IO_COUNTERS 结构（作业对象扩展信息的一部分）。"""

    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobBasicLimitInformation(ctypes.Structure):
    """Win32 JOBOBJECT_BASIC_LIMIT_INFORMATION 结构。"""

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    """Win32 JOBOBJECT_EXTENDED_LIMIT_INFORMATION 结构。"""

    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


if _IS_WINDOWS:
    _kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    _kernel32.SetInformationJobObject.restype = ctypes.c_int
    _kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
    ]
    _kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    _kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _kernel32.TerminateJobObject.restype = ctypes.c_int
    _kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _kernel32.CloseHandle.restype = ctypes.c_int
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class JobObject:
    """Windows 作业对象包装：用于内核级终止整棵服务进程树（非 Windows 上为空实现）。"""

    def __init__(self):
        """初始化（句柄为空，需先 create）。"""
        self._handle = None

    @classmethod
    def create(cls):
        """
        创建带「句柄关闭即终止」限制的作业对象。

        Returns:
            JobObject 实例；当前平台不支持或创建失败时返回 None。
        """
        if not _IS_WINDOWS:
            return None
        job = cls()
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            logging.debug("创建作业对象失败，错误码 %s", ctypes.get_last_error())
            return None
        job._handle = handle
        info = _JobExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ):
            logging.debug("设置作业对象限制失败，错误码 %s", ctypes.get_last_error())
            job.close()
            return None
        return job

    def assign(self, process) -> bool:
        """
        把子进程加入作业（其后代进程默认一并加入）。

        Args:
            process: subprocess.Popen 实例。

        Returns:
            True 表示加入成功。
        """
        if self._handle is None or process is None:
            return False
        handle = getattr(process, "_handle", None)
        if not handle:
            return False
        if not _kernel32.AssignProcessToJobObject(self._handle, ctypes.c_void_p(int(handle))):
            logging.debug("加入作业对象失败，错误码 %s", ctypes.get_last_error())
            return False
        return True

    def terminate(self) -> None:
        """终止作业内全部进程（幂等）。"""
        if self._handle is None:
            return
        if not _kernel32.TerminateJobObject(self._handle, 1):
            logging.debug("终止作业对象失败，错误码 %s", ctypes.get_last_error())

    def close(self) -> None:
        """关闭作业句柄（KILL_ON_JOB_CLOSE 生效时，残留成员随之被终止）。"""
        if self._handle is None:
            return
        try:
            _kernel32.CloseHandle(self._handle)
        finally:
            self._handle = None

    @property
    def active(self) -> bool:
        """作业句柄是否有效。"""
        return self._handle is not None

# ---- 服务状态机 ----
STATUS_STOPPED = "stopped"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_STOPPING = "stopping"
STATUS_ERROR = "error"

# ---- 就绪判定方式 ----
READY_HTTP = "http"
READY_PORT = "port"
READY_LOG = "log"
READY_PROCESS = "process"
READY_MODES = (READY_HTTP, READY_PORT, READY_LOG, READY_PROCESS)

# 就绪轮询间隔（秒）
READY_POLL_INTERVAL = 0.4
# 读取子进程输出的块大小
READ_CHUNK_SIZE = 65536
# 内部环形缓冲行数（仅用于就绪正则匹配与错误提示，前端历史由 LogHub 负责）
INTERNAL_BUFFER_LINES = 400

# 启动头部行的前缀：头部含完整命令行（可能自带 URL、token），
# 因此不进入「就绪正则 / 访问地址提取」的匹配范围，只写给人和前端看
HEADER_PREFIX = "[WebDesktop]"


class ServiceStartError(Exception):
    """服务启动阶段失败（可执行文件不存在、权限不足等）。"""


def _looks_like_python(exe: str) -> bool:
    """
    判断可执行文件是否为 Python 解释器（python.exe / pythonw.exe / py.exe）。

    Args:
        exe: 可执行文件路径。

    Returns:
        True 表示疑似 Python 解释器。
    """
    name = os.path.basename(exe or "").strip().lower()
    return name in ("python.exe", "pythonw.exe", "py.exe") or name.startswith("python3")


def build_child_env(exe: str, extra: dict = None) -> dict:
    """
    构造子进程环境变量。

    对 Python 服务自动补齐三个「只被 Python 读取、对其它程序无副作用」的变量，
    否则子进程输出会按系统 ANSI 代码页（中文系统为 GBK）编码、并按块缓冲，
    表现为日志乱码与「半天不出日志」：
    - `PYTHONUNBUFFERED=1`：逐行刷新，保证日志实时；
    - `PYTHONIOENCODING=utf-8`：stdout/stderr 按 UTF-8 编码；
    - `PYTHONUTF8=1`：启用 UTF-8 模式。

    Args:
        exe: 子进程可执行文件路径。
        extra: 配置中显式指定的环境变量（优先级最高）。

    Returns:
        完整环境变量字典。
    """
    env = dict(os.environ)
    if _looks_like_python(exe):
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
    for key, value in (extra or {}).items():
        if value is None:
            # 值为 null 表示删除该变量（便于屏蔽父进程继承来的变量）
            env.pop(str(key), None)
        else:
            env[str(key)] = str(value)
    return env


def decode_output(raw: bytes) -> str:
    """
    解码子进程输出：优先 UTF-8，失败时回退系统首选编码（中文 Windows 为 cp936）。

    子进程编码不可控（旧脚本可能输出 GBK），逐行「先 UTF-8 再回退」是最稳的折中。

    Args:
        raw: 原始字节。

    Returns:
        解码后的文本（无法解码的字节以替换字符呈现）。
    """
    for encoding in ("utf-8", locale.getpreferredencoding(False) or "utf-8"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


@dataclasses.dataclass
class ServiceSpec:
    """单个服务的启动与就绪规格（由项目配置解析得到）。"""

    #: 服务名（项目内唯一，如 backend / frontend）
    name: str
    #: 可执行文件绝对路径（如 python.exe、node.exe）
    exe: str
    #: 参数列表（不使用 shell 解析，逐项传递，天然免疫空格与转义问题）
    args: list = dataclasses.field(default_factory=list)
    #: 工作目录
    cwd: str = ""
    #: 追加/覆盖的环境变量
    env: dict = dataclasses.field(default_factory=dict)
    #: 监听端口（健康检查用，0 表示未知）
    port: int = 0
    #: 健康检查路径（如 /health；为空表示直接请求根路径）
    health: str = ""
    #: 就绪判定方式：http / port / log / process
    ready_when: str = READY_HTTP
    #: `ready_when=log` 时用于匹配就绪标志的正则
    ready_regex: str = ""
    #: 等待就绪的超时秒数
    ready_timeout: float = 60.0
    #: `ready_when=process` 时进程存活多久视为就绪
    ready_delay: float = 1.0
    #: 依赖的前置服务名（前置就绪后才启动本服务）
    depends_on: list = dataclasses.field(default_factory=list)
    #: 是否显示控制台窗口（仅调试用，默认 false）
    show_console: bool = False
    #: 退出程序时是否终止该服务
    kill_on_exit: bool = True

    def command(self) -> list:
        """
        构造子进程命令行参数列表。

        Returns:
            `[exe, *args]`。
        """
        return [self.exe, *self.args]

    def display(self) -> str:
        """
        生成用于展示的命令行文本。

        Returns:
            Windows 风格转义后的命令行字符串。
        """
        return subprocess.list2cmdline(self.command())

    def ready_url(self) -> str:
        """
        生成健康检查地址。

        Returns:
            `http://127.0.0.1:<port><health>`；未配置端口时返回空字符串。
        """
        port = int(self.port or 0)
        if port <= 0:
            return ""
        path = self.health or ""
        if path and not path.startswith("/"):
            path = "/" + path
        return f"http://127.0.0.1:{port}{path}"

    def ready_expectation(self) -> str:
        """
        生成就绪条件的中文描述（用于超时提示）。

        Returns:
            人类可读的就绪条件描述。
        """
        if self.ready_when == READY_HTTP:
            return f"HTTP 响应 {self.ready_url() or '（未配置端口）'}"
        if self.ready_when == READY_PORT:
            return f"端口 {self.port} 可连接"
        if self.ready_when == READY_LOG:
            return f"日志匹配正则 /{self.ready_regex}/"
        return f"进程稳定存活 {self.ready_delay:g} 秒"


class ServiceProcess:
    """单个服务子进程的生命周期管理（启动 / 就绪 / 日志 / 停止）。"""

    def __init__(self, spec: ServiceSpec, log_path: str = "", on_line=None):
        """
        初始化服务进程对象（不启动）。

        Args:
            spec: 服务规格。
            log_path: 日志文件路径（为空表示不落盘，仅内存日志）。
            on_line: 每读到一行日志时回调 `on_line(text)`（用于推送给前端）。
        """
        self.spec = spec
        self.log_path = log_path
        self._on_line = on_line
        self._proc = None
        self._job = None
        self._log_file = None
        self._reader = None
        self._watcher = None
        self._deque = deque(maxlen=INTERNAL_BUFFER_LINES)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._status = STATUS_STOPPED
        self._detail = ""
        self._started_at = 0.0
        self._ready_at = 0.0

    # ------------------------------------------------------------------ 状态
    @property
    def status(self) -> str:
        """当前服务状态。"""
        with self._lock:
            return self._status

    @property
    def detail(self) -> str:
        """状态补充说明（错误原因等）。"""
        with self._lock:
            return self._detail

    @property
    def pid(self):
        """子进程 PID；未启动时为 None。"""
        proc = self._proc
        return proc.pid if proc is not None else None

    def exit_code(self):
        """
        子进程退出码。

        Returns:
            退出码；进程仍在运行或未启动时返回 None。
        """
        proc = self._proc
        if proc is None:
            return None
        return proc.poll()

    def is_running(self) -> bool:
        """
        判断子进程是否存活。

        Returns:
            True 表示进程仍在运行。
        """
        proc = self._proc
        return proc is not None and proc.poll() is None

    def snapshot(self) -> dict:
        """
        生成供前端展示的状态快照。

        Returns:
            包含名称、状态、PID、端口、健康地址、命令、错误说明的字典。
        """
        with self._lock:
            status = self._status
            detail = self._detail
            started_at = self._started_at
            ready_at = self._ready_at
        return {
            "name": self.spec.name,
            "status": status,
            "detail": detail,
            "pid": self.pid,
            "exitCode": self.exit_code(),
            "port": int(self.spec.port or 0),
            "health": self.spec.ready_url(),
            "command": self.spec.display(),
            "dependsOn": list(self.spec.depends_on),
            "readyWhen": self.spec.ready_when,
            "startedAt": started_at,
            "readyAt": ready_at,
            "logPath": self.log_path,
        }

    def recent_text(self) -> str:
        """
        读取内部环形缓冲中的最近日志（就绪正则与错误提示用）。

        Returns:
            以换行连接的日志文本。
        """
        with self._lock:
            return "\n".join(list(self._deque))

    def _set_status(self, status: str, detail: str = "") -> None:
        """更新状态与说明（加锁）。"""
        with self._lock:
            self._status = status
            self._detail = detail

    # ------------------------------------------------------------------ 启动
    def start(self) -> None:
        """
        启动服务子进程并开始读取日志与等待就绪。

        Raises:
            ServiceStartError: 可执行文件不存在或进程创建失败。
        """
        if self.is_running():
            return
        exe = (self.spec.exe or "").strip()
        if not exe:
            raise ServiceStartError(f"服务「{self.spec.name}」未配置可执行文件（exe）")
        lowered = exe.lower()
        if lowered.endswith((".cmd", ".bat", ".ps1")):
            raise ServiceStartError(
                f"服务「{self.spec.name}」的可执行文件为脚本（{os.path.basename(exe)}）；"
                "按项目约束禁止调用 .cmd/.bat/.ps1，请改为直接指定解释器"
                "（如 python.exe / node.exe）加脚本参数"
            )
        if not os.path.isabs(exe):
            raise ServiceStartError(
                f"服务「{self.spec.name}」的可执行文件必须是绝对路径：{exe}"
            )
        if not os.path.exists(exe):
            raise ServiceStartError(f"服务「{self.spec.name}」的可执行文件不存在：{exe}")

        cwd = self.spec.cwd or ""
        if cwd and not os.path.isdir(cwd):
            raise ServiceStartError(f"服务「{self.spec.name}」的工作目录不存在：{cwd}")

        self._open_log(cwd)
        creation_flags = CREATE_NEW_PROCESS_GROUP
        if not self.spec.show_console:
            creation_flags |= CREATE_NO_WINDOW
        # 作业对象必须在创建进程之前建好：把「进程已启动」到「加入作业」之间的窗口压到最小，
        # 让服务自身派生的工作进程（如 next dev 拉起 node 子进程）默认一并进入作业。
        # kill_on_exit 为 false 表示「程序退出后服务继续运行」，此时不能使用带
        # KILL_ON_JOB_CLOSE 的作业（否则句柄关闭会把服务一起杀掉），因此不建作业。
        if self.spec.kill_on_exit:
            self._job = JobObject.create()
        try:
            self._proc = subprocess.Popen(
                self.spec.command(),
                cwd=cwd or None,
                env=build_child_env(exe, self.spec.env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
        except FileNotFoundError as exc:
            self._release_job()
            self._close_log()
            raise ServiceStartError(
                f"服务「{self.spec.name}」启动失败：找不到可执行文件 {exe}"
            ) from exc
        except OSError as exc:
            self._release_job()
            self._close_log()
            raise ServiceStartError(f"服务「{self.spec.name}」启动失败：{exc}") from exc

        if self._job is not None and not self._job.assign(self._proc):
            # 加入作业失败（如宿主已把子进程放进不允许嵌套的作业）：放弃作业兜底，
            # 仍靠 taskkill /T 与 kill() 清理，不影响服务本身启动
            self._release_job()

        self._stop_event.clear()
        self._started_at = time.time()
        self._ready_at = 0.0
        self._set_status(STATUS_STARTING, "进程已创建，等待就绪")
        logging.info(
            "服务已启动：project 服务=%s PID=%s 命令=%s",
            self.spec.name,
            self._proc.pid,
            self.spec.display(),
        )
        self._reader = threading.Thread(
            target=self._read_loop, name=f"svc-log-{self.spec.name}", daemon=True
        )
        self._reader.start()
        self._watcher = threading.Thread(
            target=self._watch_ready, name=f"svc-ready-{self.spec.name}", daemon=True
        )
        self._watcher.start()

    def _open_log(self, cwd: str) -> None:
        """打开服务日志文件（截断旧内容）并写入启动头部信息。"""
        if not self.log_path:
            return
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            self._log_file = open(self.log_path, "wb")
        except OSError as exc:
            logging.warning("无法创建服务日志文件 %s：%s", self.log_path, exc)
            self._log_file = None
            return
        header = (
            f"{HEADER_PREFIX} ===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动服务 {self.spec.name} =====\n"
            f"{HEADER_PREFIX} 命令: {self.spec.display()}\n"
            f"{HEADER_PREFIX} 工作目录: {cwd or '(未设置)'}\n\n"
        )
        self._write_log(header.encode("utf-8"))
        for line in header.rstrip("\n").splitlines():
            # system=True：只推送给前端与磁盘，不进入就绪/地址提取的匹配缓冲
            self._emit(line, system=True)

    def _write_log(self, raw: bytes) -> None:
        """把原始字节写入日志文件（保留子进程原始编码）。"""
        if self._log_file is None:
            return
        try:
            self._log_file.write(raw)
            self._log_file.flush()
        except OSError:
            # 磁盘写入失败不影响日志推送
            pass

    def _emit(self, text: str, system: bool = False) -> None:
        """
        记录一行日志：默认进内部缓冲并回调前端。

        Args:
            text: 日志文本。
            system: 为 True 时视为启动头部等系统行，只回调前端（不进匹配缓冲）。
        """
        if not system:
            with self._lock:
                self._deque.append(text)
        if self._on_line is not None:
            try:
                self._on_line(text)
            except Exception:
                # 前端推送异常绝不能影响日志读取线程
                logging.exception("推送服务日志失败：%s", self.spec.name)

    def _read_loop(self) -> None:
        """读取子进程输出线程：按块读取、按行切分，逐行落盘并推送。"""
        stream = self._proc.stdout if self._proc is not None else None
        if stream is None:
            return
        buffer = b""
        try:
            while True:
                chunk = stream.read1(READ_CHUNK_SIZE)
                if not chunk:
                    break
                self._write_log(chunk)
                buffer += chunk
                while True:
                    index = buffer.find(b"\n")
                    if index < 0:
                        break
                    line = buffer[:index]
                    buffer = buffer[index + 1:]
                    if line.endswith(b"\r"):
                        line = line[:-1]
                    self._emit(decode_output(line))
        except (OSError, ValueError):
            # 进程被强杀时管道读取会抛异常，属正常路径
            pass
        finally:
            if buffer:
                self._emit(decode_output(buffer))

    def _watch_ready(self) -> None:
        """等待就绪线程：轮询就绪条件，成功置 running，超时或进程退出置 error。"""
        deadline = time.monotonic() + max(0.1, float(self.spec.ready_timeout or 0))
        while not self._stop_event.is_set():
            if not self.is_running():
                code = self.exit_code()
                self._set_status(STATUS_ERROR, f"服务进程已退出（退出码：{code}），请查看日志")
                return
            if self._check_ready():
                self._ready_at = time.time()
                self._set_status(STATUS_RUNNING, "已就绪")
                logging.info("服务已就绪：%s（PID=%s）", self.spec.name, self.pid)
                return
            if time.monotonic() >= deadline:
                self._set_status(
                    STATUS_ERROR,
                    f"等待 {self.spec.ready_timeout:g} 秒后仍未就绪（就绪条件："
                    f"{self.spec.ready_expectation()}）",
                )
                return
            time.sleep(READY_POLL_INTERVAL)

    def _check_ready(self) -> bool:
        """
        执行一次就绪判定。

        Returns:
            True 表示已就绪。
        """
        mode = self.spec.ready_when
        if mode == READY_HTTP:
            return self._http_ready()
        if mode == READY_PORT:
            return self._port_ready()
        if mode == READY_LOG:
            return self._log_ready()
        if mode == READY_PROCESS:
            return (time.time() - self._started_at) >= max(0.0, float(self.spec.ready_delay or 0))
        return False

    def _http_ready(self) -> bool:
        """HTTP 健康检查：能收到任何 HTTP 响应即视为就绪（含 4xx/5xx）。"""
        url = self.spec.ready_url()
        if not url:
            return False
        try:
            request = urllib.request.Request(
                url, method="GET", headers={"User-Agent": "WebDesktop/1.0"}
            )
            with urllib.request.urlopen(request, timeout=2):
                return True
        except urllib.error.HTTPError:
            # 收到 HTTP 错误码说明服务已经在监听并响应
            return True
        except Exception:
            return False

    def _port_ready(self) -> bool:
        """TCP 端口探测：能建立连接即视为就绪。"""
        port = int(self.spec.port or 0)
        if port <= 0:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            return False

    def _log_ready(self) -> bool:
        """日志正则判定：最近日志中匹配到就绪标志。"""
        pattern = self.spec.ready_regex
        if not pattern:
            return False
        try:
            return re.search(pattern, self.recent_text()) is not None
        except re.error:
            return False

    def wait_settled(self, timeout: float) -> str:
        """
        阻塞等待服务进入「已就绪」或「错误」状态（供依赖顺序编排使用）。

        Args:
            timeout: 最长等待秒数。

        Returns:
            最终状态（running / error / stopped / starting）。
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            status = self.status
            if status in (STATUS_RUNNING, STATUS_ERROR, STATUS_STOPPED):
                return status
            time.sleep(READY_POLL_INTERVAL)
        return self.status

    # ------------------------------------------------------------------ 停止
    def stop(self, timeout: float = 10.0) -> None:
        """
        停止服务：按「taskkill → 作业对象 → kill」顺序终止整棵进程树，复位状态。

        Args:
            timeout: taskkill 超时秒数。
        """
        proc = self._proc
        if proc is None:
            self._release_job()
            self._set_status(STATUS_STOPPED, "")
            self._close_log()
            return
        self._stop_event.set()
        self._set_status(STATUS_STOPPING, "正在停止")
        if proc.poll() is None:
            if self.spec.kill_on_exit:
                self._kill_process_tree(proc, timeout)
            else:
                logging.info("服务 %s 配置为不随程序退出，保持后台运行", self.spec.name)
                self._set_status(STATUS_RUNNING, "程序退出后继续运行")
                return
        if self._reader is not None:
            # 进程已被终止，管道随子进程退出关闭，读取线程随即结束
            self._reader.join(timeout=3)
            self._reader = None
        self._close_pipe(proc)
        self._proc = None
        self._release_job()
        self._close_log()
        self._set_status(STATUS_STOPPED, "")

    def _kill_process_tree(self, proc: subprocess.Popen, timeout: float) -> None:
        """
        终止进程及其全部子进程：三道保险依次尝试，直到进程确认退出。

        1. `taskkill /F /T /PID`：按运行中的进程树逐层终止，能覆盖「加入作业之前」
           就已经派生的孙进程（常规桌面环境最可靠）；
        2. 作业对象 `TerminateJobObject`：内核对作业成员的终止，不受跨进程权限影响
           （实测受限环境下 taskkill 会被「拒绝访问」，作业对象仍能终止）；
        3. `Popen.kill()`：直接终止持有的子进程句柄，最后兜底。

        Args:
            proc: 目标进程。
            timeout: taskkill 执行超时秒数。
        """
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                logging.info("已通过 taskkill 结束服务进程树：%s PID=%s", self.spec.name, proc.pid)
            else:
                message = decode_output(result.stderr or b"").strip() or decode_output(
                    result.stdout or b""
                ).strip()
                logging.info(
                    "taskkill 未能结束进程树（返回码 %s）：%s", result.returncode, message
                )
        except Exception as exc:
            logging.warning("执行 taskkill 失败（%s）：%s", self.spec.name, exc)
        if self._wait_exit(proc, 2.0):
            return

        if self._job is not None and self._job.active:
            self._job.terminate()
            logging.info("已通过作业对象终止服务进程树：%s PID=%s", self.spec.name, proc.pid)
            if self._wait_exit(proc, 2.0):
                return

        try:
            proc.kill()
            logging.info("已通过 kill() 终止服务进程：%s PID=%s", self.spec.name, proc.pid)
        except Exception as exc:
            logging.warning("kill() 终止服务失败（%s）：%s", self.spec.name, exc)
        self._wait_exit(proc, 3.0)

    @staticmethod
    def _wait_exit(proc: subprocess.Popen, seconds: float) -> bool:
        """
        等待进程退出。

        Args:
            proc: 目标进程。
            seconds: 最长等待秒数。

        Returns:
            True 表示进程已退出。
        """
        try:
            proc.wait(timeout=seconds)
            return True
        except subprocess.TimeoutExpired:
            return False

    def _close_pipe(self, proc: subprocess.Popen) -> None:
        """
        关闭子进程输出管道（避免文件句柄与读取线程泄漏）。

        Args:
            proc: 目标进程。
        """
        stream = getattr(proc, "stdout", None)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def _release_job(self) -> None:
        """关闭作业对象句柄（KILL_ON_JOB_CLOSE 生效时，残留成员随之被内核终止）。"""
        if self._job is not None:
            self._job.close()
            self._job = None

    def _close_log(self) -> None:
        """关闭日志文件句柄。"""
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
