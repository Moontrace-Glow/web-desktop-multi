"""WebView2 内核偏好设置：恢复 pywebview 关闭的默认能力，并调整内核右键菜单。

pywebview 以 debug=False 启动时会关闭内核右键菜单与浏览器快捷键，本模块按需补开；
同时把内核 UI 调成浅色，并从右键菜单中隐藏对本应用无意义的项（后退 / 前进 / 更多工具）。
所有设置都必须访问内核对象，而内核对象只能在 UI 线程内使用，因此统一经
window.native.Invoke 封送到 UI 线程执行。
"""

import logging
import re

# WebView2 内核设置项名称（对应 CoreWebView2Settings 的属性名）
SETTING_CONTEXT_MENUS = "AreDefaultContextMenusEnabled"
SETTING_ACCELERATOR_KEYS = "AreBrowserAcceleratorKeysEnabled"

# 需要从右键菜单中隐藏的项：按内部名称匹配（名称清单见 WebView2Feedback #3297）
# back / forward 会退回加载页（等待页或错误页），对本应用无意义
HIDDEN_MENU_ITEM_NAMES = frozenset({"back", "forward"})

# 需要从右键菜单中隐藏的项：按显示文本匹配
# 「更多工具」子菜单没有公开的内部名称，只能按文本匹配（中英文各取一种写法）
HIDDEN_MENU_ITEM_LABELS = frozenset({"更多工具", "More tools"})

# 菜单显示文本的助记符后缀（如「更多工具(&T)」中的「(&T)」），比较前需剔除
_MNEMONIC_SUFFIX = re.compile(r"[（(]&?[A-Za-z][)）]\s*$")

# 事件委托强引用：pythonnet 委托一旦被 GC 回收，已订阅的事件会静默失效
_HANDLER_REFS = []

# 菜单过滤是否已订阅（进程内只订阅一次，避免每次页面加载叠加处理器）
_menu_filter_installed = False

# 菜单项清单是否已记录（仅首次右键记录一次，便于核对内核实际的菜单项名称）
_menu_items_logged = False
_browser_process = None


def apply_kernel_preferences(window, *, accelerator_keys: bool = True) -> bool:
    """
    应用 WebView2 内核偏好（单次封送到 UI 线程，依次完成三件事）。

    1. 恢复内核默认能力：pywebview 在 EdgeChromium 后端把
       AreDefaultContextMenusEnabled / AreBrowserAcceleratorKeysEnabled 都绑定到 debug 开关，
       本项目以 debug=False 启动，导致右键不弹菜单、Ctrl+R / F5 等浏览器快捷键失效；
    2. 内核 UI（右键菜单、对话框、提示框）改用浅色配色；
    3. 从右键菜单中隐藏后退 / 前进 / 更多工具等对本应用无意义的项。

    这些设置只在窗口创建时被 pywebview 写入一次，此处改写后不会被再次覆盖。
    内核对象必须在 UI 线程内访问（跨线程访问会抛线程错误），而 pywebview 的 loaded
    事件在子线程触发，因此经 window.native.Invoke 封送；委托写法与 pywebview 内部
    （webview/platforms/winforms.py）保持一致。

    Args:
        window: pywebview 窗口对象。
        accelerator_keys: 是否同时恢复浏览器快捷键（Ctrl+R / F5 刷新、Ctrl+F 查找、
            Ctrl+P 打印、Ctrl+加号减号缩放）。

    Returns:
        True 表示内核设置已写入；False 表示当前环境不支持或内核尚未就绪
        （调用方可在下次页面加载时重试）。
    """
    native = _get_native(window)
    if native is None:
        return False

    try:
        # pythonnet 已由 pywebview 的 winforms 模块加载；延迟导入避免启动期强依赖
        from System import Func, Type
    except Exception:
        logging.debug("pythonnet 不可用，跳过 WebView2 内核偏好设置")
        return False

    # 封送到 UI 线程后的写入结果（Invoke 为同步调用，返回后即可读取）
    applied = False

    def apply_preferences() -> None:
        """在 UI 线程内写入内核设置并订阅菜单事件。"""
        nonlocal applied
        # CoreWebView2 的就绪检查也必须在 UI 线程内，跨线程读取会在初始化时死锁。
        core = _get_core(native)
        if core is None:
            # 非 EdgeChromium 后端（如 MSHTML）或内核尚未初始化完成
            return
        settings = getattr(core, "Settings", None)
        if settings is None:
            return
        _track_browser_process(core)
        # 1. 内核默认能力：右键菜单恒开，浏览器快捷键按参数决定
        setattr(settings, SETTING_CONTEXT_MENUS, True)
        if accelerator_keys:
            setattr(settings, SETTING_ACCELERATOR_KEYS, True)
        # 2. 内核 UI 使用浅色配色
        _apply_light_color_scheme(core)
        # 3. 隐藏右键菜单中无意义的项
        _subscribe_menu_filter(core)
        applied = True

    try:
        native.Invoke(Func[Type](apply_preferences))
    except Exception:
        # 窗口销毁中等异常：不影响主流程，仅记录日志
        logging.exception("应用 WebView2 内核偏好失败")
        return False
    return applied


def _track_browser_process(core) -> None:
    global _browser_process
    if _browser_process is None:
        from System.Diagnostics import Process

        _browser_process = Process.GetProcessById(core.BrowserProcessId)


def wait_for_browser_exit() -> None:
    global _browser_process
    process = _browser_process
    _browser_process = None
    if process is None:
        return
    try:
        # 普通模式关闭窗口后仍可能短暂占用配置目录，退出后才能删除临时数据。
        if not process.WaitForExit(10000):
            raise TimeoutError("等待 WebView2 退出超时，无法清理临时浏览器数据")
    finally:
        process.Dispose()


def _get_native(window):
    """
    取窗口的 WinForms 原生对象。

    Args:
        window: pywebview 窗口对象。

    Returns:
        原生窗体对象；窗口尚未创建或当前不是 WinForms 后端时返回 None。
    """
    native = getattr(window, "native", None)
    if native is None or not hasattr(native, "Invoke"):
        logging.debug("窗口原生对象不可用，跳过 WebView2 内核设置")
        return None
    return native


def _get_core(native):
    """
    取 WebView2 的 CoreWebView2 对象（只能在 UI 线程内调用）。

    Args:
        native: WinForms 原生窗体对象。

    Returns:
        CoreWebView2 对象；后端不支持或内核尚未初始化完成时返回 None。
    """
    control = getattr(native, "webview", None)
    return getattr(control, "CoreWebView2", None) if control is not None else None


def _apply_light_color_scheme(core) -> None:
    """在 UI 线程设置浅色偏好；InPrivate 的原生菜单可能仍使用深色。"""
    profile = getattr(core, "Profile", None)
    if profile is None:
        # 内核版本过旧：跳过浅色设置，其余设置照常生效
        logging.warning("内核未提供 Profile 对象，跳过浅色配色设置")
        return
    try:
        from Microsoft.Web.WebView2.Core import CoreWebView2PreferredColorScheme

        # 设置前后读回并记录：用于区分「赋值未生效」与「赋值成功但内核不采纳」
        previous = profile.PreferredColorScheme
        profile.PreferredColorScheme = CoreWebView2PreferredColorScheme.Light
        logging.info("内核配色方案：%s -> %s", previous, profile.PreferredColorScheme)
    except Exception:
        # 枚举不可用等：不影响其余设置，仅记录日志
        logging.exception("设置内核浅色配色失败")


def _subscribe_menu_filter(core) -> None:
    """
    订阅右键菜单弹出事件以隐藏无意义的菜单项（幂等：进程内只订阅一次）。

    Args:
        core: CoreWebView2 对象（只能在 UI 线程内调用）。
    """
    global _menu_filter_installed
    if _menu_filter_installed:
        return
    try:
        from System import EventHandler
        from Microsoft.Web.WebView2.Core import CoreWebView2ContextMenuRequestedEventArgs

        handler = EventHandler[CoreWebView2ContextMenuRequestedEventArgs](_on_context_menu_requested)
        core.ContextMenuRequested += handler
        # 保持强引用：委托被回收后事件会静默失效
        _HANDLER_REFS.append(handler)
        _menu_filter_installed = True
    except Exception:
        # 订阅失败只影响菜单项过滤，右键菜单本身仍可正常弹出
        logging.exception("订阅内核右键菜单事件失败")


def _on_context_menu_requested(sender, args) -> None:
    """
    右键菜单弹出前的回调：记录一次菜单项清单，并隐藏无意义的项。

    Args:
        sender: 事件源（CoreWebView2）。
        args: CoreWebView2ContextMenuRequestedEventArgs 对象，MenuItems 为可增删的菜单项集合。
    """
    global _menu_items_logged
    try:
        items = args.MenuItems
        if not _menu_items_logged:
            # 只记录一次：便于核对内核实际的菜单项名称（「更多工具」等无公开内部名称）
            _menu_items_logged = True
            logging.info("内核右键菜单项：%s", _describe_menu_items(items))
        # 倒序遍历：删除元素后剩余索引仍然有效
        index = items.Count - 1
        while index >= 0:
            if _should_hide_menu_item(items[index]):
                items.RemoveAt(index)
            index -= 1
    except Exception:
        # 菜单结构变化等异常不应阻塞菜单弹出
        logging.exception("隐藏内核右键菜单项失败")


def _should_hide_menu_item(item) -> bool:
    """
    判断菜单项是否需要隐藏。

    优先按内部名称匹配（内核稳定标识），名称未收录时退回按显示文本匹配
    （「更多工具」子菜单没有公开的内部名称）。

    Args:
        item: CoreWebView2ContextMenuItem 对象。

    Returns:
        True 表示需要从菜单中移除该项。
    """
    if (getattr(item, "Name", "") or "") in HIDDEN_MENU_ITEM_NAMES:
        return True
    return _normalize_menu_label(getattr(item, "Label", "")) in HIDDEN_MENU_ITEM_LABELS


def _normalize_menu_label(label) -> str:
    """
    规范化菜单项显示文本：剔除助记符后缀（「(&T)」）与 & 标记，便于与常量比较。

    Args:
        label: CoreWebView2ContextMenuItem.Label 的原始文本。

    Returns:
        规范化后的文本（原文为空时返回空字符串）。
    """
    text = _MNEMONIC_SUFFIX.sub("", label or "")
    return text.replace("&", "").strip()


def _describe_menu_items(items) -> str:
    """
    把菜单项清单格式化为「名称(显示文本, 类型)」文本（排障用）。

    Args:
        items: CoreWebView2ContextMenuItem 集合。

    Returns:
        以分号连接的清单文本；读取失败时返回已收集到的部分。
    """
    parts = []
    try:
        for index in range(items.Count):
            item = items[index]
            parts.append(
                "{0}({1}, {2})".format(
                    getattr(item, "Name", "?"),
                    getattr(item, "Label", "?"),
                    getattr(item, "Kind", "?"),
                )
            )
    except Exception:
        logging.exception("读取内核右键菜单项失败")
    return "; ".join(parts)
