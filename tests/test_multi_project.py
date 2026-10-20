"""多项目模式的单元测试：配置校验、依赖排序、日志中心、服务进程与项目管理器。

说明（本机环境适配）：
- 不使用 `tempfile`：本机受限环境里 `tempfile.mkdtemp()` 创建的目录（mode 0o700）
  拒绝后续写入，测试统一在仓库内 `.tmp-tests/` 下自建目录并尽力清理；
- 进程相关用例真实启动 `sys.executable` 子进程，验证「无窗口 + 管道 + 就绪判定 +
  进程树清理」这一整条链路，而不是用 mock 假装。
"""

import json
import os
import shutil
import socket
import sys
import time
import unittest

from app.logs import LogHub
from app.process import (
    READY_HTTP,
    READY_LOG,
    READY_PROCESS,
    STATUS_ERROR,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_STOPPED,
    ServiceProcess,
    ServiceSpec,
    ServiceStartError,
    build_child_env,
    decode_output,
)
from app.projects import (
    PROJECT_RUNNING,
    PROJECT_STOPPED,
    Project,
    ProjectManager,
    ProjectSpec,
    parse_projects,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_TMP_ROOT = os.path.join(REPO_ROOT, ".tmp-tests")

# 测试用 Python 解释器（与运行测试的解释器一致）
PYTHON = sys.executable


def make_temp_dir(name):
    """
    在仓库内创建测试临时目录（不用 tempfile，见模块 docstring）。

    Args:
        name: 子目录名（用例名）。

    Returns:
        目录绝对路径。
    """
    path = os.path.join(TEST_TMP_ROOT, name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


def free_port():
    """
    获取一个当前空闲的本地端口。

    Returns:
        端口号。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, timeout=20.0, interval=0.1):
    """
    轮询等待条件成立。

    Args:
        predicate: 无参判定函数。
        timeout: 最长等待秒数。
        interval: 轮询间隔。

    Returns:
        True 表示在超时前成立。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def wait_idle(manager, project_id, timeout=30.0):
    """
    等待项目的启停编排线程结束。

    契约：同一项目的启停编排互斥，编排期间的并发请求会被明确拒绝；
    而「服务就绪」这一状态由服务工作线程先置位，编排线程随后才释放占用，
    因此等待状态之后还要等待空闲，才能安全地下一条指令。

    Args:
        manager: ProjectManager 实例。
        project_id: 项目标识。
        timeout: 最长等待秒数。

    Returns:
        True 表示已空闲。
    """
    return wait_for(lambda: not manager.is_busy(project_id), timeout)


class ParseProjectsTests(unittest.TestCase):
    """projects 配置解析与校验。"""

    def valid_project(self):
        """构造一份合法项目配置。"""
        return {
            "id": "demo",
            "name": "示例项目",
            "url": "http://127.0.0.1:8001",
            "services": [
                {
                    "name": "backend",
                    "exe": PYTHON,
                    "args": ["-c", "print('ok')"],
                    "cwd": REPO_ROOT,
                    "port": 8001,
                    "health": "/health",
                }
            ],
        }

    def test_valid_project_parses(self):
        specs, issues = parse_projects([self.valid_project()])
        self.assertEqual(issues, [])
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].id, "demo")
        self.assertEqual(specs[0].services[0].ready_when, "http")

    def test_relative_exe_is_rejected(self):
        raw = self.valid_project()
        raw["services"][0]["exe"] = "python.exe"
        _, issues = parse_projects([raw])
        self.assertTrue(any("绝对路径" in issue for issue in issues), issues)

    def test_script_exe_is_rejected(self):
        raw = self.valid_project()
        raw["services"][0]["exe"] = os.path.join(REPO_ROOT, "start.bat")
        _, issues = parse_projects([raw])
        self.assertTrue(any(".cmd/.bat/.ps1" in issue for issue in issues), issues)

    def test_duplicate_ids_are_rejected(self):
        _, issues = parse_projects([self.valid_project(), self.valid_project()])
        self.assertTrue(any("id 重复" in issue for issue in issues), issues)

    def test_http_ready_requires_port(self):
        # 显式声明 readyWhen=http 却没给端口 → 必须报错（未声明时缺端口会自动退化为 process 存活判定）
        raw = self.valid_project()
        raw["services"][0].pop("port")
        raw["services"][0]["readyWhen"] = "http"
        _, issues = parse_projects([raw])
        self.assertTrue(any("必须配置 port" in issue for issue in issues), issues)

    def test_ready_when_defaults_to_process_without_port(self):
        # 未声明 readyWhen 且没有端口：默认用「进程稳定存活」判定，不应报错
        raw = self.valid_project()
        raw["services"][0].pop("port")
        specs, issues = parse_projects([raw])
        self.assertEqual(issues, [])
        self.assertEqual(specs[0].services[0].ready_when, "process")

    def test_log_ready_requires_regex(self):
        raw = self.valid_project()
        raw["services"][0]["readyWhen"] = "log"
        _, issues = parse_projects([raw])
        self.assertTrue(any("readyRegex" in issue for issue in issues), issues)

    def test_unknown_dependency_is_rejected(self):
        raw = self.valid_project()
        raw["services"][0]["dependsOn"] = ["nope"]
        _, issues = parse_projects([raw])
        self.assertTrue(any("依赖的服务不存在" in issue for issue in issues), issues)

    def test_dependency_cycle_is_rejected(self):
        raw = self.valid_project()
        raw["services"].append(
            {
                "name": "frontend",
                "exe": PYTHON,
                "args": ["-c", "print('ok')"],
                "readyWhen": "process",
                "dependsOn": ["backend"],
            }
        )
        raw["services"][0]["dependsOn"] = ["frontend"]
        _, issues = parse_projects([raw])
        self.assertTrue(any(("循环" in issue) or ("未就绪" in issue) for issue in issues), issues)

    def test_invalid_port_is_rejected(self):
        raw = self.valid_project()
        raw["services"][0]["port"] = 70000
        _, issues = parse_projects([raw])
        self.assertTrue(any("port" in issue for issue in issues), issues)

    def test_ids_must_be_path_safe(self):
        raw = self.valid_project()
        raw["id"] = "../evil"
        _, issues = parse_projects([raw])
        self.assertTrue(any("id 只能包含" in issue for issue in issues), issues)


class TopoOrderTests(unittest.TestCase):
    """依赖拓扑排序，以及快照里的启动顺序信息（界面据此可视化）。"""

    def make_spec(self, services):
        """构造只用于排序的 ProjectSpec。"""
        return ProjectSpec(id="p", name="p", services=services)

    def test_dependencies_start_first(self):
        frontend = ServiceSpec(name="frontend", exe=PYTHON, depends_on=["backend"])
        backend = ServiceSpec(name="backend", exe=PYTHON)
        order = [spec.name for spec in self.make_spec([frontend, backend]).topo_order()]
        self.assertLess(order.index("backend"), order.index("frontend"))

    def test_cycle_raises(self):
        a = ServiceSpec(name="a", exe=PYTHON, depends_on=["b"])
        b = ServiceSpec(name="b", exe=PYTHON, depends_on=["a"])
        with self.assertRaises(ValueError):
            self.make_spec([a, b]).topo_order()

    def test_snapshot_exposes_start_order_and_dependency(self):
        """快照里每个服务带启动序号，项目级带启动顺序链（供界面画依赖关系）。"""
        work = make_temp_dir("topo-snapshot")
        try:
            frontend = ServiceSpec(
                name="frontend",
                exe=PYTHON,
                args=["-c", "print('x')"],
                ready_when=READY_PROCESS,
                depends_on=["backend"],
            )
            backend = ServiceSpec(
                name="backend", exe=PYTHON, args=["-c", "print('x')"], ready_when=READY_PROCESS
            )
            project = Project(self.make_spec([frontend, backend]), log_dir=work)
            snapshot = project.snapshot()
            self.assertEqual(snapshot["startOrder"], ["backend", "frontend"])
            orders = {item["name"]: item["order"] for item in snapshot["services"]}
            self.assertEqual(orders, {"backend": 1, "frontend": 2})
            front = [item for item in snapshot["services"] if item["name"] == "frontend"][0]
            self.assertEqual(front["dependsOn"], ["backend"])
            back = [item for item in snapshot["services"] if item["name"] == "backend"][0]
            self.assertEqual(back["dependsOn"], [])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_snapshot_survives_cycle_without_crashing(self):
        """即使配置出现循环（校验层会拦，但快照不能因此崩），也应退化为配置顺序。"""
        work = make_temp_dir("topo-cycle")
        try:
            a = ServiceSpec(name="a", exe=PYTHON, args=["-c", "print('x')"], depends_on=["b"])
            b = ServiceSpec(name="b", exe=PYTHON, args=["-c", "print('x')"], depends_on=["a"])
            project = Project(self.make_spec([a, b]), log_dir=work)
            snapshot = project.snapshot()
            self.assertEqual([item["name"] for item in snapshot["services"]], ["a", "b"])
            self.assertEqual(snapshot["startOrder"], ["a", "b"])
        finally:
            shutil.rmtree(work, ignore_errors=True)


class LogHubTests(unittest.TestCase):
    """日志中心：环形缓冲、增量偏移与待推送队列。"""

    def test_since_returns_incremental_lines(self):
        hub = LogHub(capacity=5)
        hub.write("p/s", "one")
        hub.write("p/s", "two")
        first = hub.since("p/s", 0)
        self.assertEqual(first["lines"], ["one", "two"])
        self.assertEqual(first["offset"], 2)
        hub.write("p/s", "three")
        second = hub.since("p/s", first["offset"])
        self.assertEqual(second["lines"], ["three"])

    def test_capacity_drops_oldest_and_reports_dropped(self):
        hub = LogHub(capacity=2)
        for index in range(5):
            hub.write("p/s", f"line{index}")
        result = hub.since("p/s", 0)
        self.assertEqual(result["lines"], ["line3", "line4"])
        self.assertEqual(result["dropped"], 3)

    def test_take_pending_is_atomic_with_offsets(self):
        hub = LogHub()
        hub.write("p/s", "a")
        first = hub.take_pending()
        self.assertEqual(first["p/s"]["offset"], 1)
        self.assertEqual(first["p/s"]["lines"], ["a"])
        self.assertEqual(hub.take_pending(), {})
        hub.write("p/s", "b")
        second = hub.take_pending()
        self.assertEqual(second["p/s"]["offset"], 2)

    def test_make_key_and_clear(self):
        hub = LogHub()
        key = LogHub.make_key("p", "s")
        self.assertEqual(key, "p/s")
        hub.write(key, "x")
        hub.clear(key)
        self.assertEqual(hub.since(key, 0)["lines"], [])


class ChildEnvTests(unittest.TestCase):
    """子进程环境变量与输出解码。"""

    def test_python_service_gets_utf8_and_unbuffered(self):
        env = build_child_env(os.path.join("C:\\", "Python312", "python.exe"))
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["PYTHONUTF8"], "1")

    def test_non_python_service_env_untouched(self):
        env = build_child_env("C:\\node\\node.exe")
        self.assertNotIn("PYTHONUNBUFFERED", env)

    def test_explicit_env_overrides_and_null_deletes(self):
        env = build_child_env("C:\\node\\node.exe", {"PYTHONUTF8": "0", "PATH": None})
        self.assertEqual(env["PYTHONUTF8"], "0")
        self.assertNotIn("PATH", env)

    def test_decode_output_falls_back_to_locale(self):
        self.assertEqual(decode_output("中文日志".encode("utf-8")), "中文日志")
        self.assertEqual(decode_output("中文日志".encode("gbk")), "中文日志")


class ServiceProcessTests(unittest.TestCase):
    """服务进程：启动、日志实时回调、就绪判定、退出与错误状态。"""

    def test_missing_exe_raises(self):
        spec = ServiceSpec(name="bad", exe=os.path.join(REPO_ROOT, "not-exist.exe"))
        process = ServiceProcess(spec)
        with self.assertRaises(ServiceStartError):
            process.start()

    def test_script_exe_raises(self):
        spec = ServiceSpec(name="bad", exe=os.path.join(REPO_ROOT, "x.cmd"))
        with self.assertRaises(ServiceStartError):
            ServiceProcess(spec).start()

    def test_log_ready_and_realtime_lines(self):
        work = make_temp_dir("svc-log")
        lines = []
        spec = ServiceSpec(
            name="printer",
            exe=PYTHON,
            args=["-c", "import time;print('READY-MARK', flush=True);time.sleep(30)"],
            cwd=work,
            ready_when=READY_LOG,
            ready_regex="READY-MARK",
            ready_timeout=20,
        )
        process = ServiceProcess(
            spec, log_path=os.path.join(work, "printer.log"), on_line=lines.append
        )
        try:
            process.start()
            self.assertTrue(
                wait_for(lambda: process.status == STATUS_RUNNING, timeout=25),
                f"未能就绪，当前状态 {process.status}：{process.detail}",
            )
            self.assertTrue(wait_for(lambda: any("READY-MARK" in line for line in lines), 5))
            self.assertIsNotNone(process.pid)
        finally:
            process.stop()
        self.assertEqual(process.status, STATUS_STOPPED)
        # 日志同时落盘（供事后排查）
        with open(os.path.join(work, "printer.log"), "rb") as handle:
            content = handle.read().decode("utf-8", "replace")
        self.assertIn("READY-MARK", content)
        shutil.rmtree(work, ignore_errors=True)

    def test_process_exit_marks_error_with_code(self):
        work = make_temp_dir("svc-exit")
        spec = ServiceSpec(
            name="crasher",
            exe=PYTHON,
            args=["-c", "import sys;sys.exit(3)"],
            cwd=work,
            ready_when=READY_PROCESS,
            ready_timeout=10,
        )
        process = ServiceProcess(spec, log_path=os.path.join(work, "crasher.log"))
        try:
            process.start()
            self.assertTrue(wait_for(lambda: process.status == STATUS_ERROR, timeout=15))
            self.assertIn("退出码：3", process.detail)
        finally:
            process.stop()
        shutil.rmtree(work, ignore_errors=True)

    def test_process_tree_is_cleaned_on_stop(self):
        """
        硬约束验证：停止服务后，孙进程也必须停止（不能留下僵尸进程）。

        做法：子进程派生一个「每 0.3 秒往心跳文件追加一行」的孙进程；
        停止服务后观察心跳文件是否停止增长。本机沙箱里 taskkill 会被拒绝访问，
        该用例同时验证作业对象兜底是否真的生效。
        """
        work = make_temp_dir("svc-tree")
        heartbeat = os.path.join(work, "heartbeat.txt")
        grandchild = (
            "import time\n"
            f"path = r'{heartbeat}'\n"
            "while True:\n"
            "    with open(path, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('tick\\n')\n"
            "    time.sleep(0.3)\n"
        )
        child = (
            "import subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
            "time.sleep(60)\n"
        )
        spec = ServiceSpec(
            name="tree",
            exe=PYTHON,
            args=["-c", child],
            cwd=work,
            ready_when=READY_PROCESS,
            ready_delay=0.5,
            ready_timeout=15,
        )
        process = ServiceProcess(spec, log_path=os.path.join(work, "tree.log"))
        try:
            process.start()
            self.assertTrue(
                wait_for(lambda: process.status == STATUS_RUNNING, timeout=20), process.detail
            )
            self.assertTrue(wait_for(lambda: os.path.exists(heartbeat), timeout=10), "孙进程未启动")
            time.sleep(0.9)
            self.assertGreater(os.path.getsize(heartbeat), 0)
            process.stop()
            time.sleep(0.6)
            middle = os.path.getsize(heartbeat)
            time.sleep(1.3)
            after = os.path.getsize(heartbeat)
            self.assertEqual(after, middle, "停止后孙进程仍在写心跳：进程树未被完整清理")
        finally:
            process.stop()
            shutil.rmtree(work, ignore_errors=True)

    def test_http_ready_on_real_listener(self):
        work = make_temp_dir("svc-http")
        port = free_port()
        code = (
            "import http.server,socketserver;"
            "socketserver.TCPServer.allow_reuse_address=True;"
            f"socketserver.TCPServer(('127.0.0.1',{port}),http.server.SimpleHTTPRequestHandler).serve_forever()"
        )
        spec = ServiceSpec(
            name="web",
            exe=PYTHON,
            args=["-c", code],
            cwd=work,
            port=port,
            ready_timeout=30,
        )
        process = ServiceProcess(spec, log_path=os.path.join(work, "web.log"))
        try:
            process.start()
            self.assertTrue(
                wait_for(lambda: process.status == STATUS_RUNNING, timeout=35),
                f"HTTP 就绪判定失败：{process.status} / {process.detail}",
            )
        finally:
            process.stop()
        self.assertFalse(process.is_running())
        shutil.rmtree(work, ignore_errors=True)


class ProjectManagerTests(unittest.TestCase):
    """项目管理器：启停编排、依赖顺序、状态派生与日志分发。"""

    def build_manager(self, name, services, stop_on_failure=False):
        """构造一个只含单项目的管理器，并返回 (manager, log_dir)。"""
        work = make_temp_dir(name)
        spec = ProjectSpec(id="demo", name="示例", stop_on_failure=stop_on_failure, services=services)
        logs = []
        manager = ProjectManager(
            [spec],
            log_dir=work,
            on_log=lambda project_id, service_name, line: logs.append((service_name, line)),
        )
        return manager, work, logs

    def test_start_then_stop_project(self):
        services = [
            ServiceSpec(
                name="worker",
                exe=PYTHON,
                args=["-c", "import time;print('UP', flush=True);time.sleep(30)"],
                cwd=REPO_ROOT,
                ready_when=READY_LOG,
                ready_regex="UP",
                ready_timeout=20,
            )
        ]
        manager, work, logs = self.build_manager("pm-basic", services)
        try:
            result = manager.start_project("demo")
            self.assertTrue(result["ok"], result)
            self.assertTrue(
                wait_for(lambda: manager.state()["projects"][0]["status"] == PROJECT_RUNNING, 30),
                manager.state(),
            )
            self.assertTrue(wait_idle(manager, "demo"), "启停编排未在预期时间内结束")
            self.assertTrue(any("UP" in line for _, line in logs))
            result = manager.stop_project("demo")
            self.assertTrue(result["ok"], result)
            self.assertTrue(
                wait_for(lambda: manager.state()["projects"][0]["status"] == PROJECT_STOPPED, 30),
                manager.state(),
            )
            self.assertTrue(wait_idle(manager, "demo"))
        finally:
            manager.stop_all()
            shutil.rmtree(work, ignore_errors=True)

    def test_dependencies_start_in_order(self):
        order = []
        backend = ServiceSpec(
            name="backend",
            exe=PYTHON,
            args=["-c", "import time;time.sleep(30)"],
            cwd=REPO_ROOT,
            ready_when=READY_PROCESS,
            ready_delay=0.4,
            ready_timeout=15,
        )
        frontend = ServiceSpec(
            name="frontend",
            exe=PYTHON,
            args=["-c", "import time;time.sleep(30)"],
            cwd=REPO_ROOT,
            ready_when=READY_PROCESS,
            ready_delay=0.2,
            ready_timeout=15,
            depends_on=["backend"],
        )
        manager, work, _ = self.build_manager("pm-order", [frontend, backend])
        original = Project.ordered_processes

        def spy(self):
            result = original(self)
            order.extend(process.spec.name for process in result)
            return result

        Project.ordered_processes = spy
        try:
            manager.start_project("demo")
            self.assertTrue(
                wait_for(lambda: manager.state()["projects"][0]["status"] == PROJECT_RUNNING, 30),
                manager.state(),
            )
            self.assertEqual(order[:2], ["backend", "frontend"])
        finally:
            Project.ordered_processes = original
            manager.stop_all()
            shutil.rmtree(work, ignore_errors=True)

    def test_failed_service_stops_project_sequence(self):
        broken = ServiceSpec(
            name="broken",
            exe=PYTHON,
            args=["-c", "import sys;sys.exit(5)"],
            cwd=REPO_ROOT,
            ready_when=READY_PROCESS,
            ready_timeout=10,
        )
        later = ServiceSpec(
            name="later",
            exe=PYTHON,
            args=["-c", "import time;time.sleep(30)"],
            cwd=REPO_ROOT,
            ready_when=READY_PROCESS,
            ready_delay=0.2,
            ready_timeout=10,
            depends_on=["broken"],
        )
        manager, work, _ = self.build_manager("pm-fail", [broken, later])
        try:
            manager.start_project("demo")
            self.assertTrue(
                wait_for(
                    lambda: manager.state()["projects"][0]["status"] == "error",
                    timeout=30,
                ),
                manager.state(),
            )
            detail = manager.state()["projects"][0]["failure"]
            self.assertIn("broken", detail)
            # 后继服务不应被启动
            self.assertEqual(manager.get("demo").process("later").status, STATUS_STOPPED)
        finally:
            manager.stop_all()
            shutil.rmtree(work, ignore_errors=True)

    def test_cancel_during_start(self):
        """
        启动过程中可以取消：编排立即中止、已启动的服务被回滚、项目回到已停止。

        用「起了进程但永远不就绪」的服务模拟长超时场景（就绪超时 120 秒），
        验证取消是立刻生效，而不是等满超时。
        """
        slow = ServiceSpec(
            name="slow",
            exe=PYTHON,
            args=["-c", "import time;time.sleep(120)"],
            cwd=REPO_ROOT,
            port=free_port(),
            ready_when=READY_HTTP,
            ready_timeout=120,
        )
        manager, work, _ = self.build_manager("pm-cancel", [slow])
        try:
            self.assertTrue(manager.start_project("demo")["ok"])
            process = manager.get("demo").process("slow")
            self.assertTrue(
                wait_for(lambda: process.status == STATUS_STARTING, timeout=15),
                f"服务未进入启动中：{process.status}",
            )
            started_at = time.monotonic()
            result = manager.stop_project("demo")
            self.assertTrue(result["ok"], result)
            self.assertIn("取消", result["message"])
            self.assertTrue(
                wait_for(lambda: not manager.is_busy("demo"), 20), "编排未在预期时间内结束"
            )
            self.assertLess(
                time.monotonic() - started_at, 15, "取消应立即生效，而不是等满就绪超时"
            )
            self.assertEqual(process.status, STATUS_STOPPED, "被取消后服务应已停止")
            self.assertEqual(manager.state()["projects"][0]["status"], PROJECT_STOPPED)
        finally:
            manager.stop_all()
            shutil.rmtree(work, ignore_errors=True)

    def test_unknown_project_returns_error(self):
        manager, work, _ = self.build_manager("pm-unknown", [])
        try:
            self.assertFalse(manager.start_project("nope")["ok"])
            self.assertFalse(manager.stop_project("nope")["ok"])
            self.assertFalse(manager.restart_project("nope")["ok"])
        finally:
            shutil.rmtree(work, ignore_errors=True)


class HomeOverrideTests(unittest.TestCase):
    """便携模式：WEBDESKTOP_HOME 覆盖配置目录并迁移默认路径。"""

    def test_config_with_utf8_bom_is_still_readable(self):
        """配置文件带 UTF-8 BOM（记事本默认行为）时也必须能正常读取。"""
        work = make_temp_dir("bom-config")
        previous = os.environ.get("WEBDESKTOP_HOME")
        os.environ["WEBDESKTOP_HOME"] = work
        try:
            from app.config import load_config

            payload = {
                "projects": [
                    {
                        "id": "bom",
                        "name": "带 BOM 的项目",
                        "services": [
                            {
                                "name": "svc",
                                "exe": PYTHON,
                                "args": ["-c", "print('ok')"],
                                "readyWhen": "process",
                            }
                        ],
                    }
                ]
            }
            path = os.path.join(work, "config.json")
            with open(path, "w", encoding="utf-8-sig") as handle:
                json.dump(payload, handle, ensure_ascii=False)

            config = load_config()
            self.assertEqual(
                [project["id"] for project in config["projects"]],
                ["bom"],
                "带 BOM 的配置必须被正常解析，而不是回落默认值",
            )
        finally:
            if previous is None:
                os.environ.pop("WEBDESKTOP_HOME", None)
            else:
                os.environ["WEBDESKTOP_HOME"] = previous
            shutil.rmtree(work, ignore_errors=True)

    def test_portable_mode_uses_data_dir_next_to_program(self):
        """程序目录下有 portable.txt 时，配置与日志都落在「程序目录/data」，不碰用户家目录。"""
        work = make_temp_dir("portable-mode")
        portable_root = os.path.join(work, "app")
        os.makedirs(portable_root)
        with open(os.path.join(portable_root, "portable.txt"), "w", encoding="utf-8") as handle:
            handle.write("portable\n")
        previous = os.environ.pop("WEBDESKTOP_HOME", None)
        try:
            from unittest.mock import patch

            from app.config import get_config_dir, load_config

            with patch("app.config.get_app_dir", return_value=portable_root):
                expected = os.path.join(portable_root, "data")
                self.assertEqual(get_config_dir(), expected)
                config = load_config()
                self.assertTrue(config["log_dir"].startswith(expected))
                self.assertTrue(
                    os.path.isfile(os.path.join(expected, "config.json")),
                    "便携模式下配置文件应创建在程序目录的 data 下",
                )
        finally:
            if previous is not None:
                os.environ["WEBDESKTOP_HOME"] = previous
            shutil.rmtree(work, ignore_errors=True)

    def test_config_dir_follows_override(self):
        work = make_temp_dir("home-override")
        previous = os.environ.get("WEBDESKTOP_HOME")
        os.environ["WEBDESKTOP_HOME"] = work
        try:
            from app.config import get_config_dir, load_config

            self.assertEqual(get_config_dir(), os.path.abspath(work))
            config = load_config()
            self.assertTrue(config["log_dir"].startswith(os.path.abspath(work)))
            self.assertTrue(config["working_dir"].startswith(os.path.abspath(work)))
            self.assertTrue(os.path.isfile(os.path.join(work, "config.json")))
        finally:
            if previous is None:
                os.environ.pop("WEBDESKTOP_HOME", None)
            else:
                os.environ["WEBDESKTOP_HOME"] = previous
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
