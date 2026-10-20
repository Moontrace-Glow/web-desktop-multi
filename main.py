"""WebDesktop 桌面启动器：多项目模式启动多个本地服务，单服务模式保持原有行为。

两种运行模式由配置决定：
- `projects` 数组非空 → 多项目启动器（项目卡片列表 + 实时日志 + 托盘动态菜单）；
- `projects` 为空 → 沿用原有单服务模式（web_command + web_url，等待页 → 跳转目标页）。
"""

import ctypes
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time

# 打包为 --windowed（无控制台）运行时，标准流可能为 None；某些依赖在 import 期会写
# stdout/stderr，一旦为 None 就会让进程静默退出（连日志都来不及写）。这里先兜底到
# devnull，且必须放在任何第三方 import 之前。
if sys.stdout is None or sys.stderr is None:
    try:
        _null_stream = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        _null_stream = None
    if _null_stream is not None:
        if sys.stdout is None:
            sys.stdout = _null_stream
        if sys.stderr is None:
            sys.stderr = _null_stream


def _emergency_report(stage: str) -> None:
    """
    不依赖任何第三方库地把当前异常写到磁盘（导入期失败时唯一能留下的线索）。

    Args:
        stage: 出错阶段描述（写入报告头部）。
    """
    import traceback

    text = (
        f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {stage} =====\n"
        f"frozen={bool(getattr(sys, 'frozen', False))} python={sys.version.split()[0]}\n"
        f"executable={sys.executable}\nargv={sys.argv}\n\n{traceback.format_exc()}"
    )
    candidates = []
    override = os.environ.get("WEBDESKTOP_HOME")
    if override:
        candidates.append(os.path.join(override, "crash.log"))
    try:
        candidates.append(
            os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "crash.log")
        )
    except Exception:
        pass
    for path in candidates:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
            return
        except OSError:
            continue


# 开发模式：优先从工作区 .site 目录加载第三方依赖（沙箱环境下系统 site-packages 不可写）
_SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".site")
if os.path.isdir(_SITE_DIR) and _SITE_DIR not in sys.path:
    sys.path.insert(0, _SITE_DIR)

try:
    import webview  # noqa: E402

    from app.config import (  # noqa: E402
        get_config_dir,
        get_config_issues,
        load_config,
    )
    from app.controller import AppController  # noqa: E402
    from app.launcher import LauncherController, create_launcher_window  # noqa: E402
    from app.pages import build_config_page, build_wait_page  # noqa: E402
    from app.tray import SystemTray  # noqa: E402
    from app.ui import create_main_window  # noqa: E402
    from app.webview_settings import wait_for_browser_exit  # noqa: E402
except Exception:
    # 导入期失败在无控制台的打包版本里完全不可见，先落盘再让异常继续抛出
    _emergency_report("导入依赖失败")
    raise

LOG_FILE_NAME = "app.log"

# 应用默认图标：根目录 app.ico（打包时由 PyInstaller 嵌入 exe）
ICON_FILE_NAME = "app.ico"

# 环境变量标记：自动重启兜底只执行一次，防止解压目录持续异常时陷入重启循环
_RESTARTED_MARK = "WEBDESKTOP_AUTO_RESTARTED"

# 多项目模式默认窗口标题
DEFAULT_LAUNCHER_TITLE = "WebDesktop 多项目启动器"

# 单实例互斥体名称（Local 会话域，仅本登录会话可见）
_INSTANCE_MUTEX_NAME = r"Local\WebDesktop_Launcher_SingleInstance"
# 句柄保留到进程结束，避免过早释放导致并发多开漏检
_instance_mutex = None


def acquire_single_instance() -> bool:
    """
    通过命名互斥体实现单实例保护。

    Returns:
        True 表示本进程是首个实例（或互斥体创建失败，不阻塞启动）；
        False 表示已有实例在运行。
    """
    global _instance_mutex
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, _INSTANCE_MUTEX_NAME)
    except Exception:
        logging.exception("创建单实例互斥体失败，跳过单实例检查")
        return True
    if not handle:
        # 资源不足等异常情况：不阻塞启动
        logging.warning("创建单实例互斥体失败（Win32 错误 %s），跳过单实例检查", ctypes.get_last_error())
        return True
    _instance_mutex = handle
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        logging.info("检测到已有启动器实例在运行")
        return False
    return True


def _activate_existing_window(title: str) -> bool:
    """
    尝试激活已存在的启动器主窗口（前台恢复显示）。

    Returns:
        True 表示已找到并激活；False 表示未找到同名窗口。
    """
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        logging.exception("激活已有启动器窗口失败")
        return False


def _prompt_already_running() -> None:
    """已有实例在运行且未找到可见窗口时，弹系统提示框引导用户查看托盘图标。"""
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            "WebDesktop 启动器已在运行。\n如果窗口不可见，请查看任务栏通知区域的托盘图标。",
            "WebDesktop 启动器",
            0x40,  # MB_ICONINFORMATION
        )
    except Exception:
        logging.warning("弹出已有实例提示失败")


def show_fatal_error(message: str) -> None:
    """
    在 GUI 环境下弹出系统错误对话框（未预期的致命错误）。

    Args:
        message: 展示给用户的错误信息。
    """
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "启动失败", 0x10)  # 0x10 = MB_ICONERROR
    except Exception:
        # 弹窗失败时退化为日志输出
        logging.critical(message)


def try_auto_restart(exc: Exception) -> bool:
    """
    解压目录被意外破坏时的兜底：以干净环境自动重启一次，避免弹出「启动失败」。

    打包后的单文件程序依赖 PyInstaller 解压目录，极端情况下该目录中的文件
    可能被外部进程删除（典型报错：Cannot find win-arm64）。此时以剥离
    _PYI_* 环境变量的方式重启，新进程会独立解压，通常即可恢复。

    Args:
        exc: main 捕获到的未预期异常。

    Returns:
        已自动重启返回 True（调用方直接退出，不再弹错误框）。
    """
    if not getattr(sys, "frozen", False):
        # 源码模式无解压目录，无需兜底
        return False
    if not isinstance(exc, FileNotFoundError) or "Cannot find" not in str(exc):
        # 仅针对解压目录文件缺失类异常兜底，其余异常仍正常提示
        return False
    if os.environ.get(_RESTARTED_MARK):
        # 已自动重启过一次仍失败：不再循环，让错误正常弹出
        return False
    try:
        # 剥离 PyInstaller 父子进程共享解压目录的环境变量，确保独立解压
        env = {key: value for key, value in os.environ.items() if not key.startswith("_PYI_")}
        env[_RESTARTED_MARK] = "1"
        subprocess.Popen(
            [sys.executable] + sys.argv[1:],
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        logging.warning("解压目录异常（%s），已自动重启应用", exc)
        return True
    except Exception:
        # 自动重启失败：退化为弹窗提示
        logging.exception("自动重启失败")
        return False


def setup_logging(config: dict) -> None:
    """初始化日志：输出到配置项 log_dir 指定的目录（默认 ~/.WebDesktop/log）。"""
    log_dir = config.get("log_dir") or os.path.join(get_config_dir(), "log")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, LOG_FILE_NAME)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        # force=True：清掉此前挂载的 root handler（如 Python 3.13 日志模块在无 handler 时
        # 隐式 basicConfig 的控制台 handler），确保日志确实写入 app.log
        force=True,
    )


def get_window_icon_path():
    """
    获取窗口图标路径（标题栏与任务栏图标）。

    - 打包运行（frozen）：返回 None。此时不向 pywebview 传 icon，
      其 Windows 后端会自动从 sys.executable（即 exe 自身）提取内嵌的
      app.ico 图标，无需随包携带额外图标文件。
    - 源码运行：返回项目根目录下 app.ico 的绝对路径；文件缺失时返回 None，
      退化为系统默认图标，不影响启动。

    Returns:
        图标文件绝对路径，或 None 表示不指定。
    """
    if getattr(sys, "frozen", False):
        # 打包后 exe 已内嵌 app.ico，交给 pywebview 从 exe 资源提取
        return None
    # 源码运行：app.ico 与 main.py 同位于项目根目录
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON_FILE_NAME)
    return icon_path if os.path.isfile(icon_path) else None


def run_single_service_mode(config: dict, window_icon) -> int:
    """
    单服务模式：启动配置中的 web 服务，就绪后跳转目标地址（原有行为，保持兼容）。

    Args:
        config: 应用配置字典。
        window_icon: 窗口图标路径（None 表示使用 exe 内嵌图标）。

    Returns:
        进程退出码，0 表示正常退出。
    """
    issues = get_config_issues(config)
    controller = AppController(config, config_mode=bool(issues))
    if issues:
        # 配置缺失或存在空参数（如首次运行）：首页进入配置页面
        logging.warning("配置不完整，进入配置页面：%s", "；".join(issues))
        create_main_window(controller, config, build_config_page(config))
    else:
        create_main_window(
            controller, config, build_wait_page(config["web_url"], config.get("window_title"))
        )

    tray = SystemTray(
        title=config.get("window_title", "Web 桌面启动器"),
        icon_path=window_icon or "",
        on_open=controller.show_window,
        on_quit=controller.exit_app,
    )
    tray.start()
    try:
        with tempfile.TemporaryDirectory(prefix="webdesktop-") as storage_path:
            try:
                webview.start(
                    controller.start,
                    debug=False,
                    icon=window_icon,
                    private_mode=config["private_mode"] is True,
                    storage_path=storage_path,
                )
            finally:
                wait_for_browser_exit()
    finally:
        # 窗口已关闭：停止后台服务进程，并移除托盘图标
        controller.stop()
        tray.stop()
    logging.info("程序退出")
    return 0


def run_launcher_mode(config: dict, window_icon) -> int:
    """
    多项目模式：主窗口为项目卡片列表，按需启停各项目的服务。

    Args:
        config: 应用配置字典（含非空 projects 数组）。
        window_icon: 窗口图标路径（None 表示使用 exe 内嵌图标）。

    Returns:
        进程退出码，0 表示正常退出。
    """
    controller = LauncherController(config)
    issues = controller.config_issues()
    if issues:
        logging.warning("项目配置存在问题（共 %d 条）：%s", len(issues), "；".join(issues))
    create_launcher_window(controller, config)

    # 托盘常驻：右键菜单动态列出各项目的启动 / 停止 / 重启 / 打开页面
    tray = SystemTray(
        title=config.get("window_title") or DEFAULT_LAUNCHER_TITLE,
        icon_path=window_icon or "",
        on_open=controller.show_window,
        on_quit=controller.exit_app,
        menu_provider=controller.build_tray_menu,
    )
    tray.start()
    # 注入托盘引用：退出看门狗在强退前需要先移除托盘图标
    controller.set_tray(tray)

    # WebView 存储目录固定放在配置目录下：界面主题等 localStorage 需要跨次运行保留
    storage_path = os.path.join(get_config_dir(), "webview")
    try:
        os.makedirs(storage_path, exist_ok=True)
    except OSError:
        logging.warning("无法创建 WebView 存储目录，退回临时目录：%s", storage_path)
        storage_path = tempfile.mkdtemp(prefix="webdesktop-")
    try:
        try:
            webview.start(
                debug=False,
                icon=window_icon,
                private_mode=config.get("private_mode") is True,
                storage_path=storage_path,
            )
        finally:
            wait_for_browser_exit()
    finally:
        # 窗口已关闭：停止全部项目服务，并移除托盘图标
        controller.stop()
        tray.stop()
    logging.info("程序退出")
    return 0


def _selftest_finish(config: dict, lines: list, exit_code: int) -> int:
    """
    输出自检报告并返回退出码。

    `--windowed` 打包后的 exe 没有 stdout，因此报告同时写入日志目录下的
    `selftest.txt`，便于自动化验证打包产物。

    Args:
        config: 应用配置字典（取 log_dir）。
        lines: 报告行。
        exit_code: 退出码。

    Returns:
        exit_code（原样返回，便于 `return _selftest_finish(...)`）。
    """
    lines.append(f"结果: {'通过' if exit_code == 0 else '失败'}（退出码 {exit_code}）")
    text = "\n".join(lines)
    try:
        print(text)
    except Exception:
        # 无控制台时 print 可能失败，忽略
        pass
    try:
        log_dir = config.get("log_dir") or os.path.join(get_config_dir(), "log")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "selftest.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        lines.append(f"报告已写入: {path}")
    except OSError:
        logging.exception("写入自检报告失败")
    logging.info("自检结束，退出码 %s", exit_code)
    return exit_code


def _selftest_gui(lines: list) -> bool:
    """
    弹出一个 3 秒自动关闭的窗口，验证 WebView2 渲染与 js_api 桥可用。

    这是打包产物最容易被忽略的环节（pythonnet / WebView2 运行时 / 隐藏导入），
    因此单独提供可自动化的界面自检。

    Args:
        lines: 报告行（就地追加）。

    Returns:
        True 表示窗口创建与关闭均成功。
    """
    try:
        window = webview.create_window(
            title="WebDesktop 自检",
            html="<h1 style=\"font-family:sans-serif\">WebView2 自检正常（3 秒后自动关闭）</h1>",
            width=460,
            height=180,
        )

        def _closer():
            time.sleep(3)
            try:
                window.destroy()
            except Exception:
                logging.debug("自检窗口关闭失败", exc_info=True)

        threading.Thread(target=_closer, name="selftest-gui", daemon=True).start()
        webview.start(debug=False)
        lines.append("WebView2 窗口: OK（已自动关闭）")
        return True
    except Exception as exc:
        lines.append(f"WebView2 窗口: 失败 {type(exc).__name__}: {exc}")
        return False


def argv_value(argv: list, flag: str) -> str:
    """
    读取 `--flag value` 形式的命令行参数值（大小写不敏感）。

    Args:
        argv: 参数列表（通常是 sys.argv）。
        flag: 参数名（如 `--project`）。

    Returns:
        参数值；不存在或缺少取值时返回空字符串。
    """
    lowered = flag.lower()
    for index, item in enumerate(argv):
        if isinstance(item, str) and item.lower() == lowered and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def run_selftest(config: dict, with_gui: bool = False, project_id: str = "") -> int:
    """
    无界面自检：验证配置解析、项目启停、日志与进程树清理这一整条链路。

    用途：打包成 `--windowed` 的 exe 后没有控制台输出，用
    `WebDesktop.exe --selftest`（或 `--selftest-gui`）即可在无人值守的情况下
    确认产物可用；结果同时写入 `<log_dir>/selftest.txt`。
    加 `--project <id>` 可指定验证哪个项目（默认验证第一个）。

    Args:
        config: 应用配置字典。
        with_gui: 是否额外做一次 WebView2 界面自检。
        project_id: 要验证的项目 ID；为空时验证第一个项目。

    Returns:
        进程退出码（0 表示自检通过）。
    """
    from app.projects import ProjectManager, parse_projects

    lines = [f"WebDesktop 自检 {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    lines.append(f"配置目录: {get_config_dir()}")
    lines.append(f"Python: {sys.version.split()[0]}，打包运行: {bool(getattr(sys, 'frozen', False))}")
    ok = True

    try:
        import webview as _webview

        lines.append(f"pywebview 导入: OK（{getattr(_webview, '__file__', '')}）")
    except Exception as exc:
        lines.append(f"pywebview 导入: 失败 {type(exc).__name__}: {exc}")
        return _selftest_finish(config, lines, 1)

    specs, issues = parse_projects(config.get("projects"))
    lines.append(f"项目数: {len(specs)}，配置问题: {len(issues)}")
    for issue in issues:
        lines.append(f"  - {issue}")
        ok = False

    if specs:
        log_dir = config.get("log_dir") or os.path.join(get_config_dir(), "log")
        manager = ProjectManager(
            specs, log_dir=os.path.join(log_dir, "projects"), on_log=lambda *args: None
        )
        if project_id:
            project = manager.get(project_id)
            if project is None:
                available = "、".join(item.spec.id for item in manager.projects())
                lines.append(f"未找到项目：{project_id}（可用项目 id：{available}）")
                return _selftest_finish(config, lines, 1)
        else:
            project = manager.projects()[0]
        index = [item.spec.id for item in manager.projects()].index(project.id)
        lines.append(
            f"测试项目: {project.spec.name}（id={project.spec.id}，共 {len(specs)} 个项目）"
        )
        result = manager.start_project(project.id)
        lines.append(f"启动请求: {result['message']}")

        def project_state():
            """读取被测项目的状态快照。"""
            return manager.state()["projects"][index]

        def settled():
            """等待编排结束。"""
            state = project_state()
            return (not state["busy"]) and state["status"] in ("running", "error", "stopped")

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and not settled():
            time.sleep(0.3)
        state = project_state()
        lines.append(f"启动后状态: {state['status']}，失败原因: {state['failure'] or '无'}")
        for service in state["services"]:
            lines.append(
                f"  服务 {service['name']}: {service['status']}"
                f" PID={service['pid']} 端口={service['port']} 就绪条件={service['readyWhen']}"
            )
        started_ok = state["status"] == "running"
        if not started_ok:
            ok = False

        manager.stop_project(project.id)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not settled():
            time.sleep(0.3)
        final = manager.state()["projects"][0]
        lines.append(f"停止后状态: {final['status']}")
        for service in final["services"]:
            if service["status"] != "stopped":
                lines.append(f"  ⚠ 服务 {service['name']} 未停止：{service['status']}")
                ok = False
        ok = ok and started_ok
        manager.stop_all()
    else:
        lines.append("没有可测试的项目：仅验证了配置解析（如需完整自检，请先配置 projects）")

    if with_gui:
        ok = _selftest_gui(lines) and ok
    return _selftest_finish(config, lines, 0 if ok else 1)


def _write_crash_report(exc: Exception) -> str:
    """
    把未预期异常的堆栈写到磁盘上的 crash.log。

    打包成 `--windowed` 后没有控制台，日志系统也可能尚未初始化，
    「先落盘、再弹窗」是这种情况下唯一能留下的线索。

    Args:
        exc: 捕获到的异常。

    Returns:
        报告文件路径；写入失败时返回空字符串。
    """
    import traceback

    text = (
        f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动失败 =====\n"
        f"frozen={bool(getattr(sys, 'frozen', False))} python={sys.version.split()[0]}\n"
        f"argv={sys.argv}\n配置目录={get_config_dir()}\n\n"
        + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    )
    for path in (
        os.path.join(get_config_dir(), "crash.log"),
        os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "crash.log"),
    ):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
            return path
        except OSError:
            continue
    return ""


def main() -> int:
    """
    程序入口：加载配置 → 自检模式或按 projects 分流多项目 / 单服务模式 → 窗口关闭后清理。

    Returns:
        进程退出码，0 表示正常退出。
    """
    try:
        # 加载配置：自动确保配置目录与配置文件存在（不存在则创建）
        config = load_config()
        setup_logging(config)
        logging.info("程序启动，配置目录：%s", get_config_dir())

        argv = [arg.lower() for arg in sys.argv[1:]]
        if "--selftest" in argv or "--selftest-gui" in argv:
            return run_selftest(
                config,
                with_gui="--selftest-gui" in argv,
                project_id=argv_value(sys.argv, "--project"),
            )

        # 单实例互斥：已有实例在运行则激活其窗口后退出（自检模式已在上方提前返回）
        if not acquire_single_instance():
            title = config.get("window_title") or DEFAULT_LAUNCHER_TITLE
            if not _activate_existing_window(title):
                _prompt_already_running()
            return 0

        # 窗口图标：打包后从 exe 内嵌资源提取 app.ico，源码运行用根目录 app.ico
        window_icon = get_window_icon_path()
        if window_icon:
            logging.info("使用窗口图标：%s", window_icon)
        else:
            logging.info(
                "窗口图标：使用 exe 内嵌图标"
                if getattr(sys, "frozen", False)
                else "未找到 app.ico，使用系统默认图标"
            )

        projects = config.get("projects") or []
        if projects:
            logging.info("进入多项目模式，共 %d 个项目", len(projects))
            return run_launcher_mode(config, window_icon)
        logging.info("进入单服务模式（projects 为空）")
        return run_single_service_mode(config, window_icon)
    except Exception as exc:  # 兜底捕获未预期异常
        # 先落盘再弹窗：--windowed 打包后没有控制台，日志系统此时也可能尚未初始化
        report = _write_crash_report(exc)
        logging.exception("程序运行发生未预期异常")
        if try_auto_restart(exc):
            return 1
        show_fatal_error(
            f"程序启动失败：{exc}"
            + (f"\n\n详细信息已写入：{report}" if report else "")
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
