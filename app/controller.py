"""应用控制器：编排「启动服务 → 等待就绪 → 跳转页面 / 错误提示」的完整流程。"""

import logging
import os
import subprocess
import sys
import threading
import time

from app.config import (
    CLOSE_ACTION_MINIMIZE_TO_TRAY,
    build_config_from_form,
    get_config_path,
    load_config,
    write_config,
)
from app.pages import (
    CLOSE_OVERLAY_SCRIPT,
    build_config_overlay_script,
    build_config_page,
    build_error_page,
    build_wait_page,
)
from app.service import WebServiceError, WebServiceManager
from app.toolbar import build_toolbar_script
from app.ui_state import load_toolbar_pos
from app.webview_settings import apply_kernel_preferences

# 等待 WebView2 内核就绪的超时与轮询间隔（秒）：内核偏好越早设置越可靠
KERNEL_READY_TIMEOUT = 10.0
KERNEL_READY_POLL_INTERVAL = 0.05


class AppController:
    """应用主控制器，管理服务生命周期、配置保存与窗口页面切换。"""

    def __init__(self, config: dict, config_mode: bool = False):
        """
        初始化控制器。

        Args:
            config: 应用配置字典。
            config_mode: 配置模式（配置缺失或存在空参数时为 True），此模式下不启动服务。
        """
        self._config = config
        self._config_mode = config_mode
        self._window = None
        self._service = None
        self._lock = threading.Lock()
        # 窗口关闭时置位，用于通知等待循环及时退出
        self.stop_event = threading.Event()
        # 是否已跳转到目标网页（仅在目标网页注入可拖动齿轮按钮）
        self._target_loaded = False
        # 内核默认能力（右键菜单、浏览器快捷键）是否已成功应用：成功一次即可
        self._kernel_defaults_applied = False
        # 配置页来源：None（首次启动）/ "web"（目标网页齿轮按钮）/ "error"（错误页）
        self._config_page_source = None
        # 最近一次错误页 HTML 缓存（配置页关闭时返回错误页用）
        self._last_error_html = None
        # 强制退出标志：托盘「退出程序」等主动退出时置位，放行窗口关闭
        self._force_exit = False

    def set_window(self, window) -> None:
        """
        绑定 GUI 窗口（由 UI 层创建后注入）。

        Args:
            window: pywebview 窗口对象。
        """
        self._window = window
        # 窗口关闭 → 置位停止事件，等待循环随即退出
        window.events.closed += self.stop_event.set
        # 每次导航完成（含刷新、返回目标网页）→ 尝试注入齿轮按钮
        window.events.loaded += self._on_page_loaded
        # 窗口关闭前 → 按「关闭窗口动作」配置决定取消关闭（最小化到托盘）或放行
        window.events.closing += self._on_window_closing
        # 内核偏好越早设置越可靠：内核就绪瞬间即应用（后台线程，不阻塞主流程）
        threading.Thread(target=self._apply_kernel_preferences_when_ready, daemon=True).start()

    def _on_window_closing(self):
        """
        窗口关闭前回调：关闭动作为「最小化到系统托盘」且非强制退出时，
        取消关闭并隐藏窗口（程序与后台服务继续运行，可从托盘恢复或退出）。

        Returns:
            False 表示取消窗口关闭；None 表示放行（正常退出）。
        """
        if self._force_exit:
            # 托盘「退出程序」等主动退出路径：放行关闭
            return None
        if self._config.get("close_action") == CLOSE_ACTION_MINIMIZE_TO_TRAY:
            try:
                # 隐藏窗口：任务栏按钮消失，程序驻留系统托盘
                self._window.hide()
            except Exception:
                # 隐藏失败（窗口可能已销毁）时记录日志，仍取消关闭以免误退
                logging.exception("隐藏窗口到系统托盘失败")
            logging.info("关闭窗口动作：最小化到系统托盘，程序继续在后台运行")
            return False
        return None

    def show_window(self) -> None:
        """托盘「打开主窗口」回调：恢复最小化状态并显示、激活主窗口。"""
        window = self._window
        if window is None:
            # 窗口尚未创建（如启动瞬间点击托盘）：忽略本次回调
            return
        try:
            # 先恢复最小化状态，再显示并激活（restore/show 均线程安全）
            window.restore()
            window.show()
            logging.info("从系统托盘恢复主窗口")
        except Exception:
            # 窗口已销毁等异常不影响托盘消息循环
            logging.exception("从系统托盘恢复主窗口失败")

    def exit_app(self) -> None:
        """错误页 / 配置页「退出」按钮及托盘「退出程序」回调：关闭窗口退出程序。"""
        logging.info("用户点击退出，关闭窗口")
        # 置位强制退出标志：放行 closing 拦截，确保窗口真正关闭
        self._force_exit = True
        self._window.destroy()

    def start(self) -> None:
        """启动流程（首次启动与错误页「重试」共用）；配置模式下不启动服务。"""
        if self._window is None or self.stop_event.is_set() or self._config_mode:
            return
        # 首屏未加载完成时切换页面，会被 WebView2 初始化时的初始 HTML 覆盖。
        while not self.stop_event.is_set():
            if self._window.events.loaded.wait(0.1):
                break
        if self.stop_event.is_set():
            return
        # 先停止可能残留的旧服务进程，避免重复启动
        self._stop_current_service()

        try:
            service = WebServiceManager(self._config)
            service.start()
        except WebServiceError as exc:
            self._show_error("服务启动失败", str(exc))
            return
        except Exception as exc:  # 兜底捕获未知异常
            logging.exception("启动服务时发生未知异常")
            self._show_error("服务启动失败", f"发生未知异常：{exc}")
            return

        with self._lock:
            self._service = service
        # 切换回等待页，并启动后台就绪检查线程
        self._target_loaded = False
        self._window.load_html(build_wait_page(self._config["web_url"], self._config.get("window_title")))
        threading.Thread(target=self._wait_loop, args=(service,), daemon=True).start()

    def retry(self) -> None:
        """错误页「重试」按钮回调（由页面 JS 经 js_api 调用）。"""
        logging.info("用户点击重试，重新启动服务")
        self.start()

    def save_config(self, data: dict) -> dict:
        """
        配置页「保存」按钮回调：校验并写入配置文件，返回结果供前端展示。

        Args:
            data: 页面提交的表单数据。

        Returns:
            {"ok": bool, "message": str} 结构的结果字典。
        """
        config, error = build_config_from_form(data)
        if error:
            logging.warning("配置保存被拒绝：%s", error)
            return {"ok": False, "message": error}
        write_config(config)
        self._config = config
        logging.info("配置已保存到：%s", get_config_path())
        return {"ok": True, "message": "配置已保存，程序即将自动重启"}

    def restart_app(self) -> None:
        """配置保存成功后重启应用：拉起新进程后关闭当前窗口（配置页 JS 延时调用）。"""
        if getattr(sys, "frozen", False):
            # PyInstaller 打包：sys.executable 即 exe 路径
            command = [sys.executable] + sys.argv[1:]
        else:
            # 源码运行：用解释器重新执行入口脚本
            command = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
        try:
            # PyInstaller onefile 通过 _PYI_* 环境变量让子进程复用父进程的解压目录，
            # 若原样继承，新旧实例会共享同一目录，旧实例退出清理时会删除新实例
            # 正在使用的文件（表现为随机报错 Cannot find win-arm64）。
            # 因此拉起新进程时剥离全部 _PYI_* 变量，让新实例独立解压、互不影响。
            env = {key: value for key, value in os.environ.items() if not key.startswith("_PYI_")}
            # CREATE_NEW_PROCESS_GROUP：新进程独立于当前进程组，不受本进程退出影响
            subprocess.Popen(command, env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            logging.info("已拉起新进程：%s", " ".join(command))
        except Exception:
            # 拉起新进程失败时配置已保存，记录日志并继续关闭窗口（用户可手动重启）
            logging.exception("拉起新进程失败")
        # 置位强制退出标志：放行 closing 拦截，确保旧实例窗口真正关闭
        self._force_exit = True
        self._window.destroy()

    def open_config_page(self, source: str = "web") -> None:
        """
        打开配置页面。

        Args:
            source: 打开来源。"error" 表示从错误页进入（先停止残留服务，
                关闭配置页后返回错误页）；"web" 表示从目标网页齿轮按钮进入，
                以全屏遮罩覆盖在目标网页上（不导航、不刷新，服务保持运行）。
        """
        logging.info("用户打开配置页面（来源：%s）", source)
        if source == "error":
            # 错误页进入：清理可能残留的服务进程（与历史行为一致），整页切换到配置页
            self._stop_current_service()
            self._config_page_source = source
            self._target_loaded = False
            self._config = load_config()
            self._window.load_html(build_config_page(self._config, show_close=True))
            return
        # 目标网页进入：注入遮罩 iframe，目标网页状态完整保留
        self._config_page_source = source
        self._config = load_config()
        overlay_html = build_config_page(self._config, show_close=True)
        try:
            self._window.evaluate_js(build_config_overlay_script(overlay_html))
        except Exception:
            # 注入失败时退化为整页打开配置页
            logging.exception("注入配置页遮罩失败，改为整页打开")
            self._target_loaded = False
            self._window.load_html(overlay_html)

    def exit_config_page(self) -> None:
        """配置页右上角「✕」按钮回调：关闭配置页，按来源返回对应页面。"""
        source = self._config_page_source
        logging.info("用户关闭配置页，返回来源：%s", source)
        self._config_page_source = None
        if source == "web":
            # 移除遮罩即可，目标网页原样保留（不导航、不刷新）
            try:
                self._window.evaluate_js(CLOSE_OVERLAY_SCRIPT)
            except Exception:
                logging.exception("移除配置页遮罩失败")
        else:
            # 返回错误页（缓存快照）；无缓存时防御性回到等待页
            self._target_loaded = False
            if self._last_error_html:
                self._window.load_html(self._last_error_html)
            else:
                self._window.load_html(build_wait_page(self._config["web_url"], self._config.get("window_title")))

    def stop(self) -> None:
        """清理资源：停止后台服务（窗口关闭后由入口调用）。"""
        self._stop_current_service()

    def _wait_loop(self, service: WebServiceManager) -> None:
        """
        后台就绪检查循环：轮询服务健康状态，就绪后跳转目标地址；
        进程退出或超时则展示错误页；窗口关闭时立即退出。

        `url_source` 为 log 时，就绪信号是服务日志中按 `url_log_regex`
        匹配到访问地址（且 http 检查通过），并用该地址跳转；
        否则沿用固定地址 web_url 跳转。

        Args:
            service: 本次启动的服务管理器实例。
        """
        timeout = self._config.get("startup_timeout", 60)
        interval = self._config.get("check_interval", 0.5)
        url_source = self._config.get("url_source", "fixed")
        url_regex = self._config.get("url_log_regex", "")
        deadline = time.monotonic() + timeout

        while not self.stop_event.is_set():
            if not service.is_running():
                # 服务进程已退出，展示退出码与日志
                self._show_error(
                    "服务已停止",
                    f"服务进程已退出（退出码：{service.exit_code()}），请检查服务日志。",
                )
                return
            if url_source == "log":
                # 从日志提取访问地址：正则匹配到且服务已可响应（http 已就绪）时才跳转
                matched_url = service.find_log_url(url_regex)
                if matched_url and service.is_ready():
                    logging.info("已从服务日志提取访问地址：%s", matched_url)
                    # 跳转目标网页：loaded 事件触发时注入可拖动齿轮按钮
                    self._target_loaded = True
                    self._window.load_url(matched_url)
                    return
            elif service.is_ready():
                logging.info("服务已就绪，跳转到 %s", self._config["web_url"])
                # 跳转目标网页：loaded 事件触发时注入可拖动齿轮按钮
                self._target_loaded = True
                self._window.load_url(self._config["web_url"])
                return
            if time.monotonic() >= deadline:
                if url_source == "log":
                    self._show_error(
                        "服务启动失败",
                        f"等待 {timeout:.0f} 秒后仍未从服务日志中提取到访问地址。\n"
                        "请检查 url_log_regex（日志提取正则）是否正确，或查看下方服务日志末尾内容以确认服务打印的地址格式。",
                    )
                else:
                    self._show_error(
                        "服务启动超时",
                        f"等待 {timeout} 秒后服务仍未就绪。\n"
                        "请检查 web_command 是否正确、web_url 是否与服务的实际端口一致。",
                    )
                return
            time.sleep(interval)

    def _show_error(self, title: str, message: str) -> None:
        """
        展示错误页，并附带服务日志末尾内容便于排查。

        Args:
            title: 错误标题。
            message: 错误说明。
        """
        log_tail = self._service.read_log_tail() if self._service else ""
        error_html = build_error_page(title, message, log_tail, window_title=self._config.get("window_title"))
        # 缓存错误页快照：配置页「✕」关闭时返回错误页
        self._last_error_html = error_html
        self._target_loaded = False
        self._window.load_html(error_html)

    def _apply_kernel_preferences_when_ready(self) -> None:
        """尽早应用内核偏好，默认菜单开关需在下一次导航时才会生效。"""
        deadline = time.monotonic() + KERNEL_READY_TIMEOUT
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            if self._kernel_defaults_applied:
                return
            if apply_kernel_preferences(self._window):
                self._kernel_defaults_applied = True
                logging.info("WebView2 内核偏好已应用（内核就绪后立即设置）")
                return
            time.sleep(KERNEL_READY_POLL_INTERVAL)
        if not self._kernel_defaults_applied:
            logging.warning(
                "等待 WebView2 内核就绪超时（%.0f 秒），改由页面加载后应用内核偏好",
                KERNEL_READY_TIMEOUT,
            )

    def _on_page_loaded(self) -> None:
        """页面加载完成回调：应用内核偏好，并在目标网页注入可拖动齿轮按钮。"""
        if self._window is None:
            return
        # 内核偏好（右键菜单与浏览器快捷键、浅色 UI、菜单项过滤）：pywebview 建窗时
        # 按 debug 关闭了前两项，此处补开；与当前页面无关，全部成功应用一次即可，
        # 失败（如内核尚未就绪）在下次加载时自动重试
        if not self._kernel_defaults_applied:
            self._kernel_defaults_applied = apply_kernel_preferences(self._window)
        if not self._target_loaded:
            return
        try:
            # 齿轮脚本自带幂等标记（默认停在右下角，可拖动，单击打开配置页）；
            # 注入时带上 Python 侧保存的位置，页面刷新与应用重启后都能还原
            self._window.evaluate_js(build_toolbar_script(load_toolbar_pos()))
            logging.info("已向目标网页注入可拖动齿轮按钮")
        except Exception:
            # 齿轮注入失败不影响主流程，仅记录日志
            logging.exception("注入可拖动齿轮按钮失败")

    def _stop_current_service(self) -> None:
        """停止当前持有的服务管理器（幂等，可重复调用）。"""
        with self._lock:
            service = self._service
            self._service = None
        if service is not None:
            service.stop()
