"""多项目启动器控制器：把 ProjectManager 的状态与日志桥接到 pywebview 前端。

与单服务模式的 :class:`app.controller.AppController` 并列：
- 单服务模式（配置中没有 `projects`）→ AppController，保持原有「等待页 → 跳转目标页」体验；
- 多项目模式（配置中有非空 `projects`）→ 本控制器，主页是项目卡片列表，项目页面另开窗口。

前端能力白名单：只能按「项目 ID / 日志键」触发启停与读取日志，
任何命令都来自 Python 侧配置，前端无法传入 exe 或参数。
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser

import webview

from app.config import get_config_path, load_config
from app.launcher_page import BACK_BUTTON_SCRIPT, build_launcher_page
from app.logs import LogHub
from app.process import HEADER_PREFIX
from app.projects import ProjectManager, parse_projects
from app.webview_settings import apply_kernel_preferences

# 推送线程的间隔（秒）：日志与状态都按此节奏批量推送
PUSH_INTERVAL = 0.15
# 状态快照的比对间隔（秒）：状态变化频率低，无需每轮都序列化比对
STATE_CHECK_INTERVAL = 0.35
# 项目窗口默认尺寸
PROJECT_WINDOW_SIZE = (1280, 860)
# 退出看门狗等待时间（秒）：正常退出流程超过该时限仍未结束，先移托盘图标再强制终止
EXIT_WATCHDOG_TIMEOUT = 15


class LauncherController:
    """多项目模式主控制器。"""

    def __init__(self, config: dict):
        """
        初始化控制器并解析项目配置。

        Args:
            config: 应用配置字典（含 projects 数组）。
        """
        self._config = config
        self._window = None
        # 托盘引用（由 main.run_launcher_mode 注入）：退出看门狗强退前需要先移除图标
        self._tray = None
        self._project_windows = {}
        # 当前在主窗口内查看的项目 ID（为空表示显示启动器页面）
        self._viewing_project = ""
        # 已自动打开过页面的项目（项目停止/异常后清除，重启可再次自动打开）
        self._auto_opened = set()
        # 前端推送失败过的动作（避免重复刷日志）
        self._push_failed = set()
        self._hub = LogHub()
        self._manager = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._force_exit = False
        self._pusher = None
        self._last_state = ""
        self._last_state_check = 0.0
        self._load_issues = []
        self._load_projects()

    # ------------------------------------------------------------ 配置与状态
    def _load_projects(self) -> None:
        """（重新）解析 projects 配置并构建项目管理器。"""
        # 重新加载配置时先关掉上一批项目窗口，避免遗留无法回收的窗口
        for window in list(self._project_windows.values()):
            try:
                window.destroy()
            except Exception:
                logging.debug("关闭旧项目窗口失败", exc_info=True)
        self._project_windows = {}
        specs, issues = parse_projects(self._config.get("projects"))
        self._load_issues = list(issues)
        for issue in issues:
            logging.error("项目配置有问题：%s", issue)
        log_dir = self._config.get("log_dir") or os.path.join(
            os.path.expanduser("~"), ".WebDesktop", "log"
        )
        self._manager = ProjectManager(
            specs,
            log_dir=os.path.join(log_dir, "projects"),
            on_log=self._on_service_log,
        )
        logging.info("已加载 %d 个项目，配置问题 %d 条", len(specs), len(issues))

    def _on_service_log(self, project_id: str, service_name: str, line: str) -> None:
        """服务日志回调：写入日志中心，等待推送线程批量送到前端。"""
        self._hub.write(LogHub.make_key(project_id, service_name), line)

    def config_issues(self) -> list:
        """
        列出项目配置问题。

        Returns:
            问题描述列表。
        """
        return list(self._load_issues)

    def state(self) -> dict:
        """
        生成前端所需的状态快照。

        Returns:
            {"projects": [...], "issues": [...], "configPath": str}。
        """
        manager = self._manager
        payload = manager.state() if manager is not None else {"projects": []}
        payload["issues"] = list(self._load_issues)
        payload["configPath"] = get_config_path()
        return payload

    # ------------------------------------------------------------ 窗口绑定
    def set_window(self, window) -> None:
        """
        绑定主窗口并启动推送线程。

        Args:
            window: pywebview 主窗口对象。
        """
        self._window = window
        window.events.closed += self._on_window_closed
        window.events.closing += self._on_window_closing
        # 每次导航完成（含项目页面）→ 注入「返回启动器」按钮并应用内核偏好
        window.events.loaded += self._on_page_loaded
        threading.Thread(target=self._apply_kernel_when_ready, daemon=True).start()
        self._pusher = threading.Thread(target=self._push_loop, name="wbd-pusher", daemon=True)
        self._pusher.start()

    def _on_page_loaded(self) -> None:
        """页面加载完成回调：查看项目页面时注入「返回启动器」悬浮按钮。"""
        window = self._window
        if window is None:
            return
        # 内核偏好（右键菜单、浏览器快捷键）对每个页面都生效，幂等且开销很小
        self._kernel_defaults_applied = apply_kernel_preferences(window)
        if not self._viewing_project:
            return
        try:
            window.evaluate_js(BACK_BUTTON_SCRIPT)
            logging.info("已注入「返回启动器」按钮（当前查看项目：%s）", self._viewing_project)
        except Exception:
            # 注入失败不影响浏览项目页面
            logging.exception("注入「返回启动器」按钮失败")

    def back_to_launcher(self) -> dict:
        """
        从项目页面返回启动器主页面。

        Returns:
            {"ok": bool, "message": str}。
        """
        window = self._window
        if window is None:
            return {"ok": False, "message": "窗口尚未就绪"}
        self._viewing_project = ""
        try:
            window.load_html(
                build_launcher_page(self.state(), self._config.get("window_title"))
            )
        except Exception as exc:
            logging.exception("返回启动器失败")
            return {"ok": False, "message": f"返回启动器失败：{exc}"}
        self._last_state = ""
        logging.info("已返回启动器主页面")
        return {"ok": True, "message": "已返回启动器"}

    def _on_window_closed(self) -> None:
        """主窗口关闭：置位停止事件，结束推送线程。"""
        self._stop_event.set()

    def _on_window_closing(self):
        """
        主窗口关闭前回调：配置为「最小化到托盘」时隐藏窗口而非退出。

        Returns:
            False 表示取消关闭；None 表示放行。
        """
        if self._force_exit:
            return None
        if self._config.get("close_action") == "minimize_to_tray":
            try:
                self._window.hide()
            except Exception:
                logging.exception("隐藏主窗口到系统托盘失败")
            return False
        return None

    def show_window(self) -> None:
        """托盘「打开主窗口」：恢复并激活主窗口。"""
        window = self._window
        if window is None:
            return
        try:
            window.restore()
            window.show()
        except Exception:
            logging.exception("从系统托盘恢复主窗口失败")

    def exit_app(self) -> dict:
        """
        退出程序：停止全部项目服务后关闭窗口。

        正常流程由 _shutdown 线程完成；若主线程卡死（如 WebView 关闭超时），
        退出看门狗在 EXIT_WATCHDOG_TIMEOUT 秒后先移除托盘图标再强制终止进程，
        避免托盘图标残留。

        Returns:
            {"ok": True, "message": str}。
        """
        logging.info("用户请求退出程序，开始停止全部项目")
        self._force_exit = True
        threading.Thread(target=self._shutdown, name="wbd-exit", daemon=True).start()
        self._schedule_exit_watchdog()
        return {"ok": True, "message": "正在停止全部服务并退出…"}

    def set_tray(self, tray) -> None:
        """
        注入托盘引用（由 main.run_launcher_mode 调用）。

        Args:
            tray: SystemTray 实例；None 表示无托盘。
        """
        self._tray = tray

    def _schedule_exit_watchdog(self) -> None:
        """
        启动退出看门狗：正常退出超时后先移除托盘图标，再强制终止进程。

        非正常退出（如 webview.start 卡死）时 finally 里的 tray.stop() 执行不到，
        NIM_DELETE 不会发出，托盘图标残留。看门狗不依赖主线程状态：
        - 先调 tray.stop()，由托盘线程执行 NIM_DELETE 移除图标；
        - 再 os._exit(0) 兜底强制终止。
        正常退出时主线程会在超时前结束进程，看门狗随进程消亡，无副作用。
        """
        def _watchdog() -> None:
            time.sleep(EXIT_WATCHDOG_TIMEOUT)
            tray = self._tray
            if tray is not None:
                try:
                    tray.stop()
                    logging.info("退出看门狗：已移除托盘图标")
                except Exception:
                    logging.exception("退出看门狗：移除托盘图标失败")
            logging.warning(
                "退出看门狗：主线程 %s 秒内未退出，强制终止进程", EXIT_WATCHDOG_TIMEOUT
            )
            os._exit(0)

        threading.Thread(target=_watchdog, name="wbd-exit-watchdog", daemon=True).start()

    def _shutdown(self) -> None:
        """停止全部服务并销毁主窗口（后台线程执行，避免阻塞前端调用）。"""
        self.stop()
        window = self._window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                logging.exception("销毁主窗口失败")

    def stop(self) -> None:
        """停止全部项目服务（幂等，退出时调用）。"""
        self._stop_event.set()
        manager = self._manager
        if manager is not None:
            manager.stop_all()
        for window in list(self._project_windows.values()):
            try:
                window.destroy()
            except Exception:
                logging.exception("关闭项目窗口失败")
        self._project_windows = {}

    # ------------------------------------------------------------ 前端接口
    def get_state(self) -> dict:
        """
        前端拉取状态快照。

        Returns:
            状态字典。
        """
        return self.state()

    def start_project(self, project_id: str) -> dict:
        """
        启动指定项目。

        Args:
            project_id: 项目标识。

        Returns:
            {"ok": bool, "message": str}。
        """
        return self._manager.start_project(str(project_id or ""))

    def stop_project(self, project_id: str) -> dict:
        """
        停止指定项目。

        Args:
            project_id: 项目标识。

        Returns:
            {"ok": bool, "message": str}。
        """
        return self._manager.stop_project(str(project_id or ""))

    def restart_project(self, project_id: str) -> dict:
        """
        重启指定项目。

        Args:
            project_id: 项目标识。

        Returns:
            {"ok": bool, "message": str}。
        """
        return self._manager.restart_project(str(project_id or ""))

    def open_project_page(self, project_id: str) -> dict:
        """
        打开项目页面（已打开则前置显示）。

        Args:
            project_id: 项目标识。

        Returns:
            {"ok": bool, "message": str}。
        """
        project = self._manager.get(str(project_id or ""))
        if project is None:
            return {"ok": False, "message": "项目不存在"}
        if project.status() != "running":
            return {"ok": False, "message": f"项目「{project.spec.name}」尚未运行"}
        url = self._resolve_project_url(project)
        if not url:
            return {"ok": False, "message": "该项目未配置打开地址（url），且日志中未提取到地址"}

        mode = project.spec.open_in
        if mode == "browser":
            webbrowser.open(url)
            logging.info("已在系统浏览器中打开项目页面：%s -> %s", project.id, url)
            return {"ok": True, "message": f"已在系统浏览器中打开「{project.spec.name}」"}
        if mode == "main":
            # 默认方式：在主窗口内导航过去，再注入「返回启动器」按钮。
            # 相比运行期另开窗口，这条路不会触发 pywebview 多窗口的稳定性问题。
            self._viewing_project = project.id
            try:
                self._window.load_url(url)
            except Exception as exc:
                self._viewing_project = ""
                logging.exception("在主窗口内打开项目页面失败")
                return {"ok": False, "message": f"打开页面失败：{exc}"}
            logging.info("已在主窗口内打开项目页面：%s -> %s", project.id, url)
            return {"ok": True, "message": f"正在打开「{project.spec.name}」"}

        existing = self._project_windows.get(project.id)
        if existing is not None:
            try:
                existing.show()
                return {"ok": True, "message": "已切到已打开的项目窗口"}
            except Exception:
                # 窗口可能已被手动关闭，回落到重新创建
                self._project_windows.pop(project.id, None)

        try:
            window = webview.create_window(
                title=project.spec.name or project.id,
                url=url,
                width=PROJECT_WINDOW_SIZE[0],
                height=PROJECT_WINDOW_SIZE[1],
                min_size=(640, 480),
                text_select=True,
            )
        except Exception:
            # 运行期创建窗口失败时退化为系统浏览器，保证「打开页面」始终可用
            logging.exception("创建项目窗口失败，改用系统浏览器打开")
            webbrowser.open(url)
            return {"ok": True, "message": "已在系统浏览器中打开项目页面"}
        self._project_windows[project.id] = window
        try:
            window.events.loaded += lambda: apply_kernel_preferences(window)
            window.events.closed += lambda: self._project_windows.pop(project.id, None)
        except Exception:
            logging.exception("绑定项目窗口事件失败")
        return {"ok": True, "message": f"已打开「{project.spec.name}」"}

    def _resolve_project_url(self, project) -> str:
        """
        解析项目的打开地址：固定地址直接用；日志来源则从各服务日志中正则提取。

        Args:
            project: 项目对象。

        Returns:
            目标地址；无法确定时返回空字符串。
        """
        if project.spec.url_source != "log":
            return project.spec.url
        pattern = project.spec.url_log_regex or r"(https?://\S+)"
        for process in reversed(project.processes()):
            text = self._hub.tail(LogHub.make_key(project.id, process.spec.name), max_lines=400)
            if not text:
                continue
            # 跳过启动头部：其中含完整命令行，命令行自带 URL 时会造成误判
            candidates = [
                line for line in text.splitlines() if not line.startswith(HEADER_PREFIX)
            ]
            if not candidates:
                continue
            try:
                match = re.search(pattern, "\n".join(candidates))
            except re.error:
                return project.spec.url
            if match:
                value = match.group(1) if match.groups() else match.group(0)
                if value and value.strip():
                    return value.strip()
        return project.spec.url

    def get_logs(self, key: str, offset: int = 0) -> dict:
        """
        读取日志增量（前端补拉历史）。

        Args:
            key: 日志键（`项目ID/服务名`）。
            offset: 前端已持有的行数偏移。

        Returns:
            {"offset": int, "dropped": int, "lines": [...]}；键非法时返回空结果。
        """
        if not self._is_valid_log_key(key):
            return {"offset": 0, "dropped": 0, "lines": []}
        try:
            return self._hub.since(key, int(offset or 0))
        except (TypeError, ValueError):
            return {"offset": 0, "dropped": 0, "lines": []}

    def clear_logs(self, key: str = "") -> dict:
        """
        清空内存日志缓冲（不删除磁盘日志）。

        Args:
            key: 日志键；为空表示清空全部。

        Returns:
            {"ok": True, "message": str}。
        """
        if key and not self._is_valid_log_key(key):
            return {"ok": False, "message": "日志键非法"}
        self._hub.clear(key)
        return {"ok": True, "message": "已清空日志显示"}

    def _is_valid_log_key(self, key: str) -> bool:
        """
        校验日志键是否指向已配置的项目与服务（白名单校验）。

        Args:
            key: 日志键。

        Returns:
            True 表示合法。
        """
        if not isinstance(key, str) or "/" not in key:
            return False
        project_id, _, service_name = key.partition("/")
        project = self._manager.get(project_id) if self._manager else None
        if project is None:
            return False
        return project.process(service_name) is not None

    def open_config_file(self) -> dict:
        """
        用系统默认程序打开配置文件（替代图形化表单编辑，避免前端可写命令）。

        Returns:
            {"ok": bool, "message": str}。
        """
        path = get_config_path()
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - 仅打开本地配置文件
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            logging.exception("打开配置文件失败")
            return {"ok": False, "message": f"打开配置文件失败：{exc}（路径：{path}）"}
        return {"ok": True, "message": f"已打开配置文件：{path}"}

    def reload_config(self) -> dict:
        """
        重新加载配置文件：仅在全部项目已停止时允许，避免半途替换配置引发状态错乱。

        Returns:
            {"ok": bool, "message": str}。
        """
        manager = self._manager
        if manager is not None:
            running = [
                project.spec.name
                for project in manager.projects()
                if project.status() not in ("stopped",)
            ]
            if running:
                return {
                    "ok": False,
                    "message": "请先停止以下项目再重新加载配置：" + "、".join(running),
                }
        config = load_config()
        # 安全性：只有解析出至少一个有效项目才允许替换当前配置。
        # 否则「配置文件被清空/写坏」时，点一下重新加载就会把界面上的项目全部清掉，
        # 而用户并不知道发生了什么。部分项目有问题时仍允许加载，问题会显示在界面顶部。
        specs, issues = parse_projects(config.get("projects"))
        if not specs:
            detail = "；".join(issues[:3]) if issues else "projects 为空或不存在"
            return {
                "ok": False,
                "message": f"配置文件里没有有效项目（{detail}），未重新加载，仍使用原配置",
            }
        self._config = config
        self._load_projects()
        self._last_state = ""
        if issues:
            return {
                "ok": True,
                "message": f"配置已重新加载，共 {len(specs)} 个项目；另有 {len(issues)} 处问题见界面顶部提示",
            }
        return {"ok": True, "message": f"配置已重新加载，共 {len(specs)} 个项目"}

    # ------------------------------------------------------------ 托盘菜单
    def build_tray_menu(self) -> list:
        """
        生成托盘右键菜单的动态项（每个项目一组：启动 / 停止 / 重启 / 打开页面）。

        Returns:
            菜单项列表，结构见 app.tray.SystemTray._build_dynamic_items。
        """
        status_text = {
            "running": "运行中",
            "starting": "启动中",
            "stopping": "停止中",
            "stopped": "已停止",
            "partial": "部分运行",
            "error": "异常",
        }
        try:
            projects = self.state().get("projects", [])
        except Exception:
            logging.exception("读取项目状态失败，托盘菜单降级为空")
            return []
        if not projects:
            return [{"label": "（尚未配置项目）", "enabled": False}]
        items = []
        for project in projects:
            project_id = project.get("id")
            status = project.get("status")
            can_open = status == "running" and bool(
                project.get("url") or project.get("urlSource") == "log"
            )
            items.append(
                {
                    "label": f"{project.get('name')} · {status_text.get(status, status)}",
                    "enabled": False,
                }
            )
            items.append(
                {
                    "label": "　▶ 启动",
                    "enabled": status == "stopped",
                    "action": (lambda pid=project_id: self.start_project(pid)),
                }
            )
            items.append(
                {
                    "label": "　■ 停止",
                    "enabled": status in ("running", "partial", "error"),
                    "action": (lambda pid=project_id: self.stop_project(pid)),
                }
            )
            items.append(
                {
                    "label": "　⟳ 重启",
                    "enabled": status != "stopped",
                    "action": (lambda pid=project_id: self.restart_project(pid)),
                }
            )
            items.append(
                {
                    "label": "　🌐 打开页面",
                    "enabled": can_open,
                    "action": (lambda pid=project_id: self.open_project_page(pid)),
                }
            )
            items.append({"separator": True})
        return items

    # ------------------------------------------------------------ 推送线程
    def _push_loop(self) -> None:
        """
        推送线程：定期检查状态并推送日志/状态。

        关键点：**业务逻辑（自动打开页面）只看 Python 侧状态，不依赖前端推送是否成功**。
        推送失败时界面仍能靠页面内的 2 秒轮询刷新，但自动打开这类动作必须自己可靠执行。
        """
        while not self._stop_event.is_set():
            time.sleep(PUSH_INTERVAL)
            window = self._window
            if window is None or self._stop_event.is_set():
                continue
            now = time.monotonic()
            if now - self._last_state_check >= STATE_CHECK_INTERVAL:
                self._last_state_check = now
                try:
                    payload = self.state()
                except Exception:
                    logging.warning("读取项目状态失败", exc_info=True)
                    continue
                self._auto_open_ready_projects(payload)
                self._push_state(window, payload)
            self._push_logs(window)

    def _note_push_failure(self, action: str) -> None:
        """
        记录前端推送失败（同一动作只完整记录一次，避免刷屏）。

        Args:
            action: 动作名（如「状态推送」）。
        """
        if action in self._push_failed:
            return
        self._push_failed.add(action)
        logging.warning(
            "前端推送失败（%s）：界面将退化为每 2 秒轮询刷新；详细堆栈见下",
            action,
            exc_info=True,
        )

    def _note_push_ok(self, action: str) -> None:
        """
        记录推送恢复正常。

        Args:
            action: 动作名。
        """
        if action in self._push_failed:
            self._push_failed.discard(action)
            logging.info("前端推送已恢复（%s）", action)

    def _push_logs(self, window) -> None:
        """推送待发送的日志增量（结构与前端偏移量校验约定一致）。"""
        pending = self._hub.take_pending()
        if not pending:
            return
        try:
            window.evaluate_js(
                "window.__wbd && window.__wbd.logs(" + json.dumps(pending, ensure_ascii=False) + ");"
            )
            self._note_push_ok("日志推送")
        except Exception:
            self._note_push_failure("日志推送")

    def _push_state(self, window, payload: dict) -> None:
        """
        状态有变化时推送状态快照。

        Args:
            window: 主窗口。
            payload: 已算好的状态快照（避免重复计算）。
        """
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if text == self._last_state:
            return
        self._last_state = text
        try:
            window.evaluate_js(
                "window.__wbd && window.__wbd.state(" + json.dumps(payload, ensure_ascii=False) + ");"
            )
            self._note_push_ok("状态推送")
        except Exception:
            self._note_push_failure("状态推送")

    def _auto_open_ready_projects(self, payload: dict) -> None:
        """
        项目刚变为「运行中」时自动打开其页面（与原版「服务就绪即跳转」体验一致）。

        判定只用 `_auto_opened` 这一个集合，避免两种坑：
        - 编排尚未结束（busy）时不能算「已处理」，否则 busy 一清除就再也没机会打开；
        - 项目回到停止/异常后清除标记，重启可再次自动打开。

        可在项目配置里用 `"autoOpen": false` 关闭。

        Args:
            payload: 最新状态快照。
        """
        for project in payload.get("projects", []):
            project_id = project.get("id")
            status = project.get("status")
            if status in ("stopped", "error"):
                self._auto_opened.discard(project_id)
                continue
            if status != "running":
                continue
            if project.get("busy") or not project.get("autoOpen", True):
                # 编排未结束或用户关掉了自动打开：等下一次状态变化再判断
                continue
            if project_id in self._auto_opened:
                continue
            self._auto_opened.add(project_id)
            result = self.open_project_page(project_id)
            if result.get("ok"):
                logging.info("项目就绪，已自动打开页面：%s", project_id)
            else:
                logging.warning(
                    "项目就绪但未能自动打开页面：%s（%s）", project_id, result.get("message")
                )

    def _apply_kernel_when_ready(self) -> None:
        """尽早应用 WebView2 内核偏好（右键菜单与浏览器快捷键）。"""
        deadline = time.monotonic() + 10.0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            if apply_kernel_preferences(self._window):
                logging.info("WebView2 内核偏好已应用（多项目主窗口）")
                return
            time.sleep(0.05)
        logging.warning("等待 WebView2 内核就绪超时，改由页面加载后应用内核偏好")


def create_launcher_window(controller: LauncherController, config: dict):
    """
    创建多项目启动器主窗口。

    Args:
        controller: 启动器控制器。
        config: 应用配置字典。

    Returns:
        pywebview 窗口对象。
    """
    size = config.get("window_size", [1200, 800])
    window = webview.create_window(
        title=config.get("window_title", "WebDesktop 多项目启动器"),
        html=build_launcher_page(controller.state(), config.get("window_title")),
        js_api=LauncherBridge(controller),
        width=int(size[0]),
        height=int(size[1]),
        min_size=(900, 620),
        text_select=True,
        background_color="#ffffff",
    )
    controller.set_window(window)
    logging.info("多项目主窗口已创建：%s（%sx%s）", window.title, size[0], size[1])
    return window


class LauncherBridge:
    """暴露给前端 JS 的白名单接口（不含任何可执行任意命令的入口）。"""

    def __init__(self, controller: LauncherController):
        """
        初始化接口。

        Args:
            controller: 启动器控制器。
        """
        self._controller = controller

    def get_state(self) -> dict:
        """拉取状态快照。"""
        return self._controller.get_state()

    def start_project(self, project_id: str) -> dict:
        """启动项目。"""
        return self._controller.start_project(project_id)

    def stop_project(self, project_id: str) -> dict:
        """停止项目。"""
        return self._controller.stop_project(project_id)

    def restart_project(self, project_id: str) -> dict:
        """重启项目。"""
        return self._controller.restart_project(project_id)

    def open_project_page(self, project_id: str) -> dict:
        """打开项目页面。"""
        return self._controller.open_project_page(project_id)

    def back_to_launcher(self) -> dict:
        """从项目页面返回启动器主页面（页面上的「返回启动器」按钮调用）。"""
        return self._controller.back_to_launcher()

    def get_logs(self, key: str, offset: int = 0) -> dict:
        """读取日志增量。"""
        return self._controller.get_logs(key, offset)

    def clear_logs(self, key: str = "") -> dict:
        """清空日志缓冲。"""
        return self._controller.clear_logs(key)

    def open_config_file(self) -> dict:
        """打开配置文件。"""
        return self._controller.open_config_file()

    def reload_config(self) -> dict:
        """重新加载配置。"""
        return self._controller.reload_config()

    def exit_app(self) -> dict:
        """退出程序。"""
        return self._controller.exit_app()
