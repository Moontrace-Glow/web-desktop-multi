"""系统托盘模块：基于 Win32 API（ctypes）实现托盘图标与右键菜单，不依赖第三方库。

托盘窗口为隐藏消息窗口，独立线程运行消息循环：
- 左键单击 / 双击托盘图标：打开主窗口
- 右键单击托盘图标：弹出菜单（打开主窗口 / 退出程序）
"""

import ctypes
import logging
import sys
import threading

# 仅支持 Windows（ctypes.WinDLL / WINFUNCTYPE 为 Windows 专属）
IS_WINDOWS = hasattr(ctypes, "WinDLL")

if IS_WINDOWS:
    # use_last_error=True：使 ctypes.get_last_error() 能取到 Win32 错误码
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # ExtractIconEx 系列位于 shell32.dll
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
else:
    _user32 = None
    _kernel32 = None
    _shell32 = None

# ---- Win32 消息常量 ----
WM_NULL = 0x0000
WM_USER = 0x0400
WM_DESTROY = 0x0002
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
# 托盘回调消息（Shell_NotifyIcon 通过 uCallbackMessage 上报鼠标事件）
WM_TRAY_CALLBACK = WM_USER + 20
# 内部退出消息：消息循环线程收到后移除图标并销毁窗口
WM_TRAY_EXIT = WM_USER + 21

# ---- 托盘图标常量 ----
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

# ---- 图标加载常量 ----
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
# 系统默认应用程序图标
IDI_APPLICATION = 32512

# ---- 弹出菜单常量 ----
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x00000002
TPM_NONOTIFY = 0x00000080
TPM_RETURNCMD = 0x00000100

# 菜单项命令 ID（TPM_RETURNCMD 模式下 TrackPopupMenu 的返回值）
MENU_ID_OPEN = 1001
MENU_ID_EXIT = 1002
# 动态菜单项 ID 起始值（由 menu_provider 提供的项目级菜单占用）
MENU_ID_DYNAMIC_BASE = 2000
# 置灰菜单项标志（动态菜单中「已运行」的项目等）
MF_GRAYED = 0x00000001

# 隐藏窗口类名与样式
TRAY_WINDOW_CLASS = "WebDesktopTrayWindow"
WS_OVERLAPPED = 0
CW_USEDEFAULT = 0x80000000

# 线程退出等待超时（秒）
TRAY_THREAD_JOIN_TIMEOUT = 3

if IS_WINDOWS:
    # 窗口过程回调类型：返回 LRESULT，参数为 (hwnd, msg, wparam, lparam)
    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_size_t,
    )
else:
    WNDPROC = None


class POINT(ctypes.Structure):
    """Win32 POINT 结构：屏幕坐标。"""

    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class MSG(ctypes.Structure):
    """Win32 MSG 结构：消息队列元素。"""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_size_t),
        ("time", ctypes.c_uint),
        ("pt", POINT),
    ]


class WNDCLASSW(ctypes.Structure):
    """Win32 WNDCLASSW 结构：窗口类定义。"""

    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """Win32 NOTIFYICONDATAW 结构：托盘图标数据（含新版气泡字段，保证 cbSize 正确）。"""

    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint),
        ("dwStateMask", ctypes.c_uint),
        ("szInfo", ctypes.c_wchar * 256),
        ("uTimeout", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


if IS_WINDOWS:
    # ---- 函数签名声明：ctypes 默认按 32 位 int 传参/返回值，64 位系统上句柄会被截断，
    # 因此必须为涉及句柄、指针、结构体的函数显式声明 argtypes / restype ----
    _user32.CreateWindowExW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _user32.CreateWindowExW.restype = ctypes.c_void_p
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    _user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    _user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
    _user32.DefWindowProcW.restype = ctypes.c_size_t
    _user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    _user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _user32.LoadImageW.restype = ctypes.c_void_p
    _user32.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _user32.LoadIconW.restype = ctypes.c_void_p
    _shell32.ExtractIconExW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
    ]
    _shell32.ExtractIconExW.restype = ctypes.c_uint
    _user32.CreatePopupMenu.restype = ctypes.c_void_p
    _user32.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p]
    _user32.TrackPopupMenuEx.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _user32.TrackPopupMenuEx.restype = ctypes.c_uint
    _user32.DestroyMenu.argtypes = [ctypes.c_void_p]
    _user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    _user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    _user32.SetForegroundWindow.restype = ctypes.c_int
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    _shell32.Shell_NotifyIconW.argtypes = [ctypes.c_uint, ctypes.POINTER(NOTIFYICONDATAW)]
    _shell32.Shell_NotifyIconW.restype = ctypes.c_int
    _kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    _kernel32.GetModuleHandleW.restype = ctypes.c_void_p


class SystemTray:
    """系统托盘：创建隐藏消息窗口并注册托盘图标，支持右键菜单与打开主窗口回调。"""

    def __init__(self, title: str, icon_path: str = "", on_open=None, on_quit=None, menu_provider=None):
        """
        初始化托盘对象（不启动，需调用 start）。

        Args:
            title: 托盘图标悬停提示文本（通常为窗口标题）。
            icon_path: 图标文件路径（ico）；为空且打包运行时自动从 exe 内嵌资源提取。
            on_open: 左键单击图标或菜单「打开主窗口」回调（无参数）。
            on_quit: 菜单「退出程序」回调（无参数）。
            menu_provider: 动态菜单项提供者，返回菜单项列表（见 _build_dynamic_items），
                用于在右键菜单中列出各项目的启动/停止操作；为 None 时菜单与旧版一致。
        """
        self._title = title
        self._icon_path = icon_path
        self._on_open = on_open
        self._on_quit = on_quit
        self._menu_provider = menu_provider
        self._hwnd = None
        self._thread = None
        # 菜单弹出中标志：防止 TrackPopupMenu 模态循环期间重复进入弹出逻辑
        self._menu_open = False
        self._wnd_proc = WNDPROC(self._handle_message)

    def start(self) -> None:
        """启动托盘：在独立线程中创建隐藏窗口、注册图标并进入消息循环。"""
        if not IS_WINDOWS:
            # 非 Windows 平台不支持托盘，仅记录日志不影响主流程
            logging.warning("当前平台不支持系统托盘，跳过托盘创建")
            return
        self._thread = threading.Thread(target=self._run, name="system-tray", daemon=True)
        self._thread.start()
        logging.info("系统托盘已启动")

    def stop(self) -> None:
        """停止托盘：通知消息循环线程移除图标并退出（幂等，可重复调用）。"""
        hwnd = self._hwnd
        self._hwnd = None
        if hwnd is None or self._thread is None:
            return
        try:
            _user32.PostMessageW(hwnd, WM_TRAY_EXIT, 0, 0)
        except Exception:
            # 消息投递失败时线程可能已退出，直接忽略
            logging.exception("通知托盘线程退出失败")
        self._thread.join(timeout=TRAY_THREAD_JOIN_TIMEOUT)
        self._thread = None
        logging.info("系统托盘已停止")

    def _run(self) -> None:
        """托盘线程主流程：注册窗口类 → 创建隐藏窗口 → 添加图标 → 消息循环。"""
        try:
            hinstance = _kernel32.GetModuleHandleW(None)
            self._register_class(hinstance)
            hwnd = self._create_window(hinstance)
            self._hwnd = hwnd
            hicon = self._load_icon()
            self._add_icon(hwnd, hicon)
            logging.info("托盘图标已添加，窗口句柄：%s", hwnd)
        except Exception:
            # 托盘创建失败不影响主程序运行，仅记录日志
            logging.exception("系统托盘创建失败")
            if self._hwnd:
                _user32.DestroyWindow(self._hwnd)
            return

        msg = MSG()
        # 消息循环：GetMessage 返回 0（收到 WM_QUIT）时退出
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _register_class(self, hinstance) -> None:
        """注册隐藏消息窗口类（类已存在时忽略，幂等）。"""
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = ctypes.cast(self._wnd_proc, ctypes.c_void_p)
        window_class.hInstance = hinstance
        window_class.lpszClassName = TRAY_WINDOW_CLASS
        result = _user32.RegisterClassW(ctypes.byref(window_class))
        # 1410 = ERROR_CLASS_ALREADY_EXISTS：类已注册（同进程重复创建）时忽略
        if result == 0 and ctypes.get_last_error() != 1410:
            raise ctypes.WinError(ctypes.get_last_error())

    def _create_window(self, hinstance):
        """创建隐藏消息窗口，返回窗口句柄。"""
        hwnd = _user32.CreateWindowExW(
            0,
            TRAY_WINDOW_CLASS,
            self._title,
            WS_OVERLAPPED,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        return hwnd

    def _load_icon(self):
        """
        加载托盘图标句柄。

        - 打包运行（frozen）：从 exe 内嵌资源提取图标；
        - 源码运行：加载指定 ico 文件；
        - 上述均失败时退化为系统默认图标。
        """
        hicon = None
        if getattr(sys, "frozen", False):
            # ExtractIconExW 从 exe 提取大图标（索引 0），用于托盘展示
            large = ctypes.c_void_p()
            small = ctypes.c_void_p()
            count = _shell32.ExtractIconExW(sys.executable, 0, ctypes.byref(large), ctypes.byref(small), 1)
            if count > 0 and large.value:
                hicon = large.value
            if small.value:
                # 小图标本次未使用，及时释放句柄避免泄漏
                _user32.DestroyIcon(small.value)
        elif self._icon_path:
            # LR_LOADFROMFILE + LR_DEFAULTSIZE：直接按文件加载 ico 并取系统默认尺寸
            hicon = _user32.LoadImageW(
                None, self._icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
        if hicon:
            return hicon
        # 兜底：系统默认应用程序图标
        return _user32.LoadIconW(None, IDI_APPLICATION)

    def _add_icon(self, hwnd, hicon) -> None:
        """通过 Shell_NotifyIcon 向系统托盘添加图标。"""
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.hIcon = hicon
        nid.szTip = self._title[:127]
        if not _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _handle_message(self, hwnd, msg, wparam, lparam):
        """窗口过程：处理托盘回调消息与内部退出消息，其余交默认处理。"""
        if msg == WM_TRAY_CALLBACK:
            # lParam 为鼠标事件消息：左键单击/双击打开主窗口，右键单击弹出菜单
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._call_safely(self._on_open, "打开主窗口")
            elif lparam == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if msg == WM_TRAY_EXIT:
            # 移除托盘图标并销毁窗口（WM_DESTROY 处理器中投递 WM_QUIT 结束消息循环）
            self._remove_icon()
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        """弹出右键菜单（打开主窗口 / 退出程序），按用户选择执行回调。"""
        if self._menu_open:
            # 菜单弹出（模态）期间收到新的右键消息：忽略，避免嵌套弹出
            return
        self._menu_open = True
        menu = _user32.CreatePopupMenu()
        if not menu:
            self._menu_open = False
            logging.error("创建托盘右键菜单失败")
            return
        try:
            _user32.AppendMenuW(menu, MF_STRING, MENU_ID_OPEN, "打开主窗口")
            # 动态项目菜单：由 menu_provider 每次弹出时现算（状态实时、无需重建托盘）
            dynamic = self._build_dynamic_items(menu)
            _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            _user32.AppendMenuW(menu, MF_STRING, MENU_ID_EXIT, "退出程序")
            # 获取鼠标当前位置作为菜单弹出位置
            point = POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            # 关键修复（微软 KB Q135788 记载的托盘菜单经典问题）：从 WM_TRAYICON
            # 回调弹出菜单时，隐藏窗口未成为前台窗口，菜单模态循环无法收到外部
            # 点击的取消消息，表现为菜单弹出后点击其他位置无法消失。
            # 1. 弹出前先让隐藏窗口成为前台窗口；
            _user32.SetForegroundWindow(self._hwnd)
            # TPM_RETURNCMD：同步返回所选命令 ID，无需处理 WM_COMMAND
            command = _user32.TrackPopupMenuEx(
                menu,
                TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                point.x,
                point.y,
                self._hwnd,
                None,
            )
            # 2. 返回后补发一条 WM_NULL，强制窗口过程完成菜单关闭的
            #    WM_CANCELMODE 清理，否则点击菜单外部时菜单无法消失。
            _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
            if command == MENU_ID_OPEN:
                self._call_safely(self._on_open, "打开主窗口")
            elif command == MENU_ID_EXIT:
                self._call_safely(self._on_quit, "退出程序")
            elif command in dynamic:
                callback, label = dynamic[command]
                self._call_safely(callback, label)
        finally:
            self._menu_open = False
            _user32.DestroyMenu(menu)

    def _build_dynamic_items(self, menu) -> dict:
        """
        构建动态菜单项（项目级启动/停止），并返回命令 ID 到回调的映射。

        menu_provider 返回的每一项为字典：
        - `label`：菜单文字（必填，超长自动截断）
        - `action`：被点击时执行的回调；为空且非分隔线时该项置灰
        - `enabled`：是否可用（默认 True）
        - `separator`：为 True 时渲染为分隔线

        Args:
            menu: 目标弹出菜单句柄。

        Returns:
            {命令ID: (回调, 标签)}；provider 缺失或异常时返回空字典。
        """
        if self._menu_provider is None:
            return {}
        try:
            items = self._menu_provider() or []
        except Exception:
            # 动态菜单生成失败不能影响托盘可用性
            logging.exception("生成托盘动态菜单失败")
            return {}
        mapping = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("separator"):
                _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            callback = item.get("action")
            enabled = bool(item.get("enabled", True)) and callable(callback)
            command_id = MENU_ID_DYNAMIC_BASE + len(mapping)
            flags = MF_STRING if enabled else (MF_STRING | MF_GRAYED)
            _user32.AppendMenuW(menu, flags, command_id, label[:60])
            if enabled:
                mapping[command_id] = (callback, label)
        return mapping

    def _remove_icon(self) -> None:
        """从系统托盘移除图标（幂等，重复删除仅返回 False 无副作用）。"""
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def _call_safely(self, callback, action: str) -> None:
        """安全执行托盘回调：回调异常只记录日志，不影响托盘线程。"""
        if callback is None:
            return
        try:
            callback()
        except Exception:
            # 回调异常（如窗口已销毁）不影响托盘消息循环
            logging.exception("托盘回调执行失败：%s", action)
