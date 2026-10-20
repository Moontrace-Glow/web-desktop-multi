"""多项目控制器集成测试：不依赖 GUI 窗口，验证后端编排、状态快照与 js_api 白名单契约。

覆盖面：
- `LauncherController` 加载项目配置 → 启动 / 停止 / 重启 → 状态快照；
- 日志中心与 `get_logs / clear_logs` 的增量与白名单校验（非法日志键必须被拒绝）；
- 「运行中不允许重新加载配置」的保护；
- 托盘动态菜单项与状态的一致性；
- `url_source=log` 时从服务日志提取访问地址。
"""

import json
import os
import shutil
import sys
import time
import unittest

from app.config import get_config_path
from app.launcher_page import build_launcher_page
from app.launcher import LauncherBridge, LauncherController
from app.logs import LogHub
from app.process import READY_PROCESS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_TMP_ROOT = os.path.join(REPO_ROOT, ".tmp-tests")
PYTHON = sys.executable

# 测试服务：打印一行就绪标记后长期存活
SERVICE_CODE = "import time;print('LAUNCHER-READY', flush=True);time.sleep(60)"


def make_temp_dir(name):
    """
    在仓库内创建测试临时目录（不使用 tempfile，见 test_multi_project 模块说明）。

    Args:
        name: 子目录名。

    Returns:
        目录绝对路径。
    """
    path = os.path.join(TEST_TMP_ROOT, name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


def wait_for(predicate, timeout=25.0, interval=0.1):
    """
    轮询等待条件成立。

    Args:
        predicate: 无参判定函数。
        timeout: 最长等待秒数。
        interval: 轮询间隔。

    Returns:
        True 表示超时前成立。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def build_config(home, url_source="fixed"):
    """
    构造一份多项目测试配置。

    Args:
        home: 配置/日志根目录。
        url_source: 项目访问地址来源（fixed / log）。

    Returns:
        配置字典。
    """
    return {
        "window_title": "多项目测试",
        "window_size": [1200, 800],
        "private_mode": False,
        "close_action": "minimize_to_tray",
        "kill_on_exit": True,
        "log_dir": os.path.join(home, "log"),
        "projects": [
            {
                "id": "demo",
                "name": "示例项目",
                "url": "http://127.0.0.1:9/",
                "url_source": url_source,
                "url_log_regex": r"(http://127\.0\.0\.1:\d+)",
                "services": [
                    {
                        "name": "worker",
                        "exe": PYTHON,
                        "args": ["-c", SERVICE_CODE],
                        "cwd": REPO_ROOT,
                        "readyWhen": "process",
                        "readyDelay": 0.3,
                        "readyTimeout": 20,
                    }
                ],
            }
        ],
    }


class LauncherControllerTests(unittest.TestCase):
    """多项目控制器与桥接口的行为契约。"""

    def setUp(self):
        self.home = make_temp_dir("launcher-home")
        self._previous_home = os.environ.get("WEBDESKTOP_HOME")
        # 隔离配置目录，避免测试碰到真实用户配置
        os.environ["WEBDESKTOP_HOME"] = self.home
        self.controller = LauncherController(build_config(self.home))
        self.bridge = LauncherBridge(self.controller)

    def tearDown(self):
        # 先等编排线程收尾，再清理，避免「启动过程中被 tearDown 停掉」产生误导性错误日志
        wait_for(lambda: not self.bridge.get_state()["projects"][0]["busy"], 10)
        self.controller.stop()
        if self._previous_home is None:
            os.environ.pop("WEBDESKTOP_HOME", None)
        else:
            os.environ["WEBDESKTOP_HOME"] = self._previous_home
        shutil.rmtree(self.home, ignore_errors=True)

    def project_status(self):
        """读取当前项目状态。"""
        return self.bridge.get_state()["projects"][0]["status"]

    def wait_status(self, expected, timeout=30):
        """等待项目进入指定状态。"""
        self.assertTrue(
            wait_for(lambda: self.project_status() == expected, timeout),
            f"未进入状态 {expected}，当前：{self.controller.state()}",
        )

    def wait_idle(self, timeout=30):
        """
        等待启停编排线程结束。

        契约：同一项目的启停编排互斥，编排期间的并发请求会被明确拒绝
        （界面上对应按钮此时也是置灰的）。因此「等待状态」之后还要「等待空闲」，
        才能安全地下一条指令。
        """
        self.assertTrue(
            wait_for(
                lambda: not self.bridge.get_state()["projects"][0]["busy"], timeout
            ),
            "启停编排未在预期时间内结束",
        )

    def test_state_snapshot_shape(self):
        state = self.bridge.get_state()
        self.assertEqual(state["issues"], [])
        self.assertEqual(len(state["projects"]), 1)
        project = state["projects"][0]
        for key in ("id", "name", "url", "status", "services", "busy"):
            self.assertIn(key, project)
        service = project["services"][0]
        for key in ("name", "status", "pid", "port", "command", "logPath"):
            self.assertIn(key, service)
        self.assertEqual(project["status"], "stopped")

    def test_start_and_stop_through_bridge(self):
        result = self.bridge.start_project("demo")
        self.assertTrue(result["ok"], result)
        self.wait_status("running")
        self.wait_idle()
        self.assertEqual(self.bridge.get_state()["projects"][0]["services"][0]["status"], "running")

        result = self.bridge.stop_project("demo")
        self.assertTrue(result["ok"], result)
        self.wait_status("stopped")
        self.wait_idle()
        self.assertIsNone(self.bridge.get_state()["projects"][0]["services"][0]["pid"])

    def test_restart_through_bridge(self):
        self.bridge.start_project("demo")
        self.wait_status("running")
        self.wait_idle()
        first_pid = self.bridge.get_state()["projects"][0]["services"][0]["pid"]
        result = self.bridge.restart_project("demo")
        self.assertTrue(result["ok"], result)
        self.wait_status("running")
        self.wait_idle()
        second_pid = self.bridge.get_state()["projects"][0]["services"][0]["pid"]
        self.assertNotEqual(first_pid, second_pid, "重启后应是新的进程")

    def test_unknown_project_is_rejected(self):
        for name in ("start_project", "stop_project", "restart_project", "open_project_page"):
            result = getattr(self.bridge, name)("not-exist")
            self.assertFalse(result["ok"], f"{name} 应拒绝未知项目")
        self.assertTrue(self.controller.state()["projects"])

    def test_open_page_requires_running_project(self):
        result = self.bridge.open_project_page("demo")
        self.assertFalse(result["ok"])
        self.assertIn("尚未运行", result["message"])

    def test_logs_incremental_and_whitelisted(self):
        self.bridge.start_project("demo")
        self.wait_status("running")
        self.wait_idle()
        key = LogHub.make_key("demo", "worker")
        self.assertTrue(
            wait_for(lambda: any("LAUNCHER-READY" in line for line in self.bridge.get_logs(key, 0)["lines"]), 15)
        )
        first = self.bridge.get_logs(key, 0)
        self.assertGreaterEqual(first["offset"], 1)
        # 偏移之后没有新日志时应返回空增量
        self.assertEqual(self.bridge.get_logs(key, first["offset"])["lines"], [])
        # 非法日志键一律拒绝，防止前端越权读取任意路径
        for bad_key in ("../../etc/passwd", "demo/unknown", "unknown/worker", "", "demo"):
            self.assertEqual(
                self.bridge.get_logs(bad_key, 0)["lines"], [], f"{bad_key!r} 应被拒绝"
            )
        self.assertFalse(self.bridge.clear_logs("bad/key")["ok"])
        self.assertTrue(self.bridge.clear_logs(key)["ok"])

    def test_reload_config_refused_while_running(self):
        self.bridge.start_project("demo")
        self.wait_status("running")
        self.wait_idle()
        result = self.bridge.reload_config()
        self.assertFalse(result["ok"], "运行中不应允许重新加载配置")
        self.assertIn("示例项目", result["message"])
        self.bridge.stop_project("demo")
        self.wait_status("stopped")
        self.wait_idle()
        self.assertEqual(self.project_status(), "stopped")

    def test_reload_config_refuses_empty_file_and_keeps_current(self):
        """配置文件里没有有效项目时，重新加载必须被拒绝，且不能把现有项目清空。"""
        result = self.bridge.reload_config()
        self.assertFalse(result["ok"], "空配置不应被接受")
        self.assertIn("没有有效项目", result["message"])
        self.assertEqual(len(self.bridge.get_state()["projects"]), 1, "原配置应保持不变")

    def test_reload_config_applies_valid_file(self):
        """配置文件里写出两个项目后，重新加载应生效。"""
        config = build_config(self.home)
        second = json.loads(json.dumps(config["projects"][0]))
        second["id"] = "demo2"
        second["name"] = "示例项目 2"
        config["projects"].append(second)
        with open(get_config_path(), "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False)

        result = self.bridge.reload_config()
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.bridge.get_state()["projects"]), 2)
        self.assertEqual(
            [project["id"] for project in self.bridge.get_state()["projects"]], ["demo", "demo2"]
        )

    def test_tray_menu_reflects_state(self):
        labels = [item.get("label", "") for item in self.controller.build_tray_menu()]
        self.assertTrue(any("示例项目" in label for label in labels))
        self.assertTrue(any("启动" in label for label in labels))
        # 未运行时「打开页面」应置灰
        open_items = [item for item in self.controller.build_tray_menu() if "打开页面" in item.get("label", "")]
        self.assertTrue(open_items)
        self.assertFalse(open_items[0]["enabled"])

        self.bridge.start_project("demo")
        self.wait_status("running")
        self.wait_idle()
        open_items = [item for item in self.controller.build_tray_menu() if "打开页面" in item.get("label", "")]
        self.assertTrue(open_items[0]["enabled"], "运行中「打开页面」应可用")

    def test_url_extracted_from_service_log(self):
        """url_source=log：地址应取自服务打印的日志，且不会被启动头部里的命令行误匹配。"""
        config = build_config(self.home, url_source="log")
        # 用默认提取正则（取整段 URL），验证「服务打印的地址」被完整取回
        config["projects"][0]["url_log_regex"] = r"(https?://\S+)"
        # 服务启动后打印一个地址；配置里的 url 是另一个（不可用的）地址，
        # 用于区分「提取成功」与「回退到配置值」
        config["projects"][0]["services"][0]["args"] = [
            "-c",
            "import time;print('服务地址 http://127.0.0.1:45678/', flush=True);time.sleep(60)",
        ]
        controller = LauncherController(config)
        try:
            bridge = LauncherBridge(controller)
            bridge.start_project("demo")
            self.assertTrue(
                wait_for(lambda: bridge.get_state()["projects"][0]["status"] == "running", 30)
            )
            self.assertTrue(
                wait_for(lambda: not bridge.get_state()["projects"][0]["busy"], 30)
            )
            project = controller._manager.get("demo")
            self.assertTrue(
                wait_for(lambda: controller._resolve_project_url(project) == "http://127.0.0.1:45678/", 10),
                f"未从日志提取到地址，当前：{controller._resolve_project_url(project)}",
            )
        finally:
            controller.stop()


    def test_command_line_url_is_not_treated_as_service_address(self):
        """
        启动头部含完整命令行，命令行里出现的 URL 不能被当成「服务打印的访问地址」。

        这里让命令行里带一个诱饵地址、但服务自己不打印任何地址：
        结果必须回退到配置的 url，而不是诱饵地址。
        """
        config = build_config(self.home, url_source="log")
        config["projects"][0]["services"][0]["args"] = [
            "-c",
            "import time  # 诱饵地址 http://127.0.0.1:99999/\ntime.sleep(60)",
        ]
        controller = LauncherController(config)
        try:
            bridge = LauncherBridge(controller)
            bridge.start_project("demo")
            self.assertTrue(
                wait_for(lambda: bridge.get_state()["projects"][0]["status"] == "running", 30)
            )
            self.assertTrue(wait_for(lambda: not bridge.get_state()["projects"][0]["busy"], 30))
            project = controller._manager.get("demo")
            resolved = controller._resolve_project_url(project)
            self.assertEqual(resolved, "http://127.0.0.1:9/", "不应把命令行里的地址当成服务地址")
        finally:
            controller.stop()


class ProjectPageRoutingTests(unittest.TestCase):
    """页面打开方式的决策逻辑（用假窗口验证路由，不依赖真实 GUI）。"""

    class FakeWindow:
        """记录 load_url / load_html / evaluate_js 调用的假窗口。"""

        def __init__(self):
            self.urls = []
            self.htmls = []
            self.scripts = []

        def load_url(self, url):
            """记录导航。"""
            self.urls.append(url)

        def load_html(self, html):
            """记录整页替换。"""
            self.htmls.append(html)

        def evaluate_js(self, script):
            """记录脚本注入。"""
            self.scripts.append(script)

    def setUp(self):
        self.home = make_temp_dir("launcher-routing")
        self._previous_home = os.environ.get("WEBDESKTOP_HOME")
        os.environ["WEBDESKTOP_HOME"] = self.home

    def tearDown(self):
        if self._previous_home is None:
            os.environ.pop("WEBDESKTOP_HOME", None)
        else:
            os.environ["WEBDESKTOP_HOME"] = self._previous_home
        shutil.rmtree(self.home, ignore_errors=True)

    def build_controller(self, open_in="main", auto_open=True):
        """
        构造控制器并直接塞入假窗口。

        直接赋值 `_window`（而不是 set_window）是为了不启动推送线程——
        否则推送线程会自行触发「就绪自动打开」，与本用例的断言相互干扰。
        """
        config = build_config(self.home)
        config["projects"][0]["openIn"] = open_in
        config["projects"][0]["autoOpen"] = auto_open
        controller = LauncherController(config)
        window = self.FakeWindow()
        controller._window = window
        return controller, window

    def test_default_mode_navigates_main_window(self):
        controller, window = self.build_controller(open_in="main")
        try:
            controller._manager.get("demo").process("worker")._set_status("running", "已就绪")
            result = controller.open_project_page("demo")
            self.assertTrue(result["ok"], result)
            self.assertEqual(window.urls, ["http://127.0.0.1:9/"])
            self.assertEqual(controller._viewing_project, "demo")
        finally:
            controller.stop()

    def test_back_to_launcher_restores_cards_page(self):
        controller, window = self.build_controller(open_in="main")
        try:
            controller._manager.get("demo").process("worker")._set_status("running", "已就绪")
            controller.open_project_page("demo")
            result = controller.back_to_launcher()
            self.assertTrue(result["ok"], result)
            self.assertEqual(controller._viewing_project, "")
            self.assertEqual(len(window.htmls), 1)
            self.assertIn("WebDesktop", window.htmls[0])
        finally:
            controller.stop()

    def test_back_button_injected_on_project_page(self):
        controller, window = self.build_controller(open_in="main")
        try:
            controller._manager.get("demo").process("worker")._set_status("running", "已就绪")
            controller.open_project_page("demo")
            window.scripts.clear()
            controller._on_page_loaded()
            self.assertTrue(window.scripts, "项目页面加载后应注入返回按钮脚本")
            self.assertIn("__wbd_back_button", window.scripts[0])
        finally:
            controller.stop()

    def test_no_back_button_on_launcher_page(self):
        controller, window = self.build_controller(open_in="main")
        try:
            window.scripts.clear()
            controller._on_page_loaded()
            self.assertEqual(window.scripts, [], "启动器页面不应注入返回按钮")
        finally:
            controller.stop()

    def test_project_snapshot_carries_page_options(self):
        controller, _ = self.build_controller(open_in="main", auto_open=False)
        try:
            project = controller.state()["projects"][0]
            self.assertEqual(project["openIn"], "main")
            self.assertFalse(project["autoOpen"])
        finally:
            controller.stop()

    def test_auto_open_triggers_once_per_run(self):
        controller, window = self.build_controller(open_in="main")
        try:
            # 自动打开最终走的是 open_project_page，需要项目真的处于运行中
            controller._manager.get("demo").process("worker")._set_status("running", "已就绪")
            self.assertEqual(window.urls, [])
            controller._auto_open_ready_projects(
                {"projects": [{"id": "demo", "status": "running", "autoOpen": True, "busy": False}]}
            )
            self.assertEqual(len(window.urls), 1, "首次就绪应自动打开页面")
            controller._auto_open_ready_projects(
                {"projects": [{"id": "demo", "status": "running", "autoOpen": True, "busy": False}]}
            )
            self.assertEqual(len(window.urls), 1, "状态未变化时不应重复打开")
            # 停止后再次就绪 → 允许再次自动打开
            controller._auto_open_ready_projects({"projects": [{"id": "demo", "status": "stopped"}]})
            controller._auto_open_ready_projects(
                {"projects": [{"id": "demo", "status": "running", "autoOpen": True, "busy": False}]}
            )
            self.assertEqual(len(window.urls), 2)
        finally:
            controller.stop()

    def test_auto_open_waits_for_busy_to_clear(self):
        """编排未结束（busy）时不能消费掉「就绪」这一次机会，否则永远不会自动打开。"""
        controller, window = self.build_controller(open_in="main")
        try:
            controller._manager.get("demo").process("worker")._set_status("running", "已就绪")
            busy_payload = {
                "projects": [{"id": "demo", "status": "running", "autoOpen": True, "busy": True}]
            }
            controller._auto_open_ready_projects(busy_payload)
            self.assertEqual(window.urls, [], "busy 期间不应打开")
            idle_payload = {
                "projects": [{"id": "demo", "status": "running", "autoOpen": True, "busy": False}]
            }
            controller._auto_open_ready_projects(idle_payload)
            self.assertEqual(len(window.urls), 1, "busy 结束后应补上这次自动打开")
        finally:
            controller.stop()

    def test_auto_open_disabled_by_config(self):
        controller, window = self.build_controller(open_in="main", auto_open=False)
        try:
            controller._auto_open_ready_projects(
                {"projects": [{"id": "demo", "status": "running", "autoOpen": False, "busy": False}]}
            )
            self.assertEqual(window.urls, [], "autoOpen=false 时不应自动打开")
        finally:
            controller.stop()


class LauncherPageAssetTests(unittest.TestCase):
    """页面资源注入：xterm.js 存在时内联进页面，缺失时优雅降级到轻量日志面板。"""

    def test_page_inlines_vendored_terminal_assets(self):
        page = build_launcher_page({"projects": []}, "测试")
        # 占位符必须全部被替换（否则页面里会残留 __WBD_xxx__）
        self.assertNotIn("__WBD_XTERM_JS__", page)
        self.assertNotIn("__WBD_XTERM_CSS__", page)
        self.assertNotIn("__WBD_TITLE__", page)
        self.assertNotIn("__WBD_BOOTSTRAP__", page)
        # 仓库内已 vendored xterm 资源 → 页面应包含终端容器与 xterm 的样式（版权头是 vendor 独有标记）
        self.assertIn("term-host", page)
        self.assertIn("The xterm.js authors", page)
        self.assertIn("new window.Terminal", page)

    def test_page_degrades_when_assets_missing(self):
        from unittest.mock import patch

        missing = os.path.join(REPO_ROOT, "no-such-asset-dir")
        with patch("app.launcher_page.get_web_asset_dir", return_value=missing):
            page = build_launcher_page({"projects": []}, "测试")
        # 轻量面板仍在；vendor 资源未注入 → 运行时 terminalAvailable() 为假，走 <pre> 渲染
        self.assertIn("log-view", page)
        self.assertIn("terminalAvailable", page)
        self.assertNotIn("The xterm.js authors", page, "资源缺失时不应注入 xterm 样式")
        self.assertNotIn("__WBD_XTERM_JS__", page)


class ExitWatchdogTests(unittest.TestCase):
    """退出看门狗（P1）：正常退出超时后先移除托盘图标，再强制终止进程。"""

    def test_watchdog_removes_tray_then_forces_exit(self):
        from unittest.mock import patch

        controller = LauncherController(build_config(make_temp_dir("watchdog-home")))
        tray = type("FakeTray", (), {"stop": lambda self: None})()
        controller.set_tray(tray)

        with patch("app.launcher.EXIT_WATCHDOG_TIMEOUT", 0.1), patch(
            "app.launcher.os._exit"
        ) as fake_exit:
            controller.exit_app()
            self.assertTrue(
                wait_for(lambda: fake_exit.call_count > 0, timeout=5),
                "退出看门狗未在超时后触发",
            )
            fake_exit.assert_called_once_with(0)

    def test_watchdog_skips_when_no_tray(self):
        from unittest.mock import patch

        controller = LauncherController(build_config(make_temp_dir("watchdog-home2")))
        with patch("app.launcher.EXIT_WATCHDOG_TIMEOUT", 0.1), patch(
            "app.launcher.os._exit"
        ) as fake_exit:
            controller.exit_app()
            self.assertTrue(
                wait_for(lambda: fake_exit.call_count > 0, timeout=5),
                "无托盘时退出看门狗仍应兜底强退",
            )
            fake_exit.assert_called_once_with(0)

    def test_set_tray_injects_reference(self):
        controller = LauncherController(build_config(make_temp_dir("watchdog-home3")))
        self.assertIsNone(controller._tray)
        tray = object()
        controller.set_tray(tray)
        self.assertIs(controller._tray, tray)


if __name__ == "__main__":
    unittest.main()
