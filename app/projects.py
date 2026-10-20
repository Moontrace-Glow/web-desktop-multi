"""项目管理模块：多项目配置解析、校验、依赖排序与启停编排。

层次关系：

    ProjectManager            管理多个项目（全局状态、日志分发）
      └── Project             一个项目 = 多个服务 + 项目级 URL
            └── ServiceProcess 一个服务 = 一个子进程（见 app.process）

设计取舍：
- 启动/停止在工作线程中串行执行，`js_api` 调用立即返回，界面不卡顿；
- 服务按 `dependsOn` 拓扑排序启动，前置服务「就绪」后才启动后继服务；
- 停止按逆序执行，降低「后端先死、前端报错刷屏」的概率；
- 项目状态由各服务状态派生，避免出现两套状态互相不一致。
"""

import dataclasses
import logging
import os
import re
import threading
import time

from app.icons import resolve_icon
from app.logs import LogHub
from app.process import (
    READY_HTTP,
    READY_LOG,
    READY_MODES,
    READY_PROCESS,
    STATUS_ERROR,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_STOPPED,
    STATUS_STOPPING,
    ServiceProcess,
    ServiceSpec,
    ServiceStartError,
)

# 项目级派生状态
PROJECT_RUNNING = "running"
PROJECT_STARTING = "starting"
PROJECT_STOPPING = "stopping"
PROJECT_STOPPED = "stopped"
PROJECT_PARTIAL = "partial"
PROJECT_ERROR = "error"

# 项目 ID 允许的字符（同时用作日志目录名，需避免路径穿越）
_PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

# 取消启动：编排线程在等待就绪时按此间隔分片检查取消请求
CANCEL_POLL_INTERVAL = 0.3
# 「已取消」的哨兵状态（与服务状态机区分开）
CANCEL_STATE = "cancelled"


@dataclasses.dataclass
class ProjectSpec:
    """单个项目的配置规格。"""

    #: 唯一标识（同时作为日志子目录名）
    id: str
    #: 显示名称
    name: str
    #: 图标路径（可选，支持本地文件或 URL，前端直接用于 <img>）
    icon: str = ""
    #: 项目就绪后打开的页面地址
    url: str = ""
    #: 访问地址来源：fixed=使用 url / log=从服务日志正则提取
    url_source: str = "fixed"
    #: url_source=log 时的提取正则
    url_log_regex: str = r"(https?://\S+)"
    #: 启动失败时是否自动停止本项目已启动的服务
    stop_on_failure: bool = False
    #: 项目就绪后是否自动打开页面（与原版「就绪即跳转」体验一致）
    auto_open: bool = True
    #: 页面打开方式：main=在主窗口内打开（默认，稳定）/ window=另开窗口（实验性）/ browser=系统浏览器
    open_in: str = "main"
    #: 服务列表（配置顺序，实际启动顺序由依赖决定）
    services: list = dataclasses.field(default_factory=list)

    def service_names(self) -> list:
        """
        列出本项目全部服务名。

        Returns:
            服务名列表。
        """
        return [service.name for service in self.services]

    def topo_order(self) -> list:
        """
        按 `dependsOn` 做拓扑排序（Kahn 算法，同层保持配置顺序）。

        Returns:
            拓扑序的 ServiceSpec 列表。

        Raises:
            ValueError: 存在环时抛出（配置校验阶段已拦截，此处为兜底）。
        """
        by_name = {service.name: service for service in self.services}
        indegree = {service.name: 0 for service in self.services}
        for service in self.services:
            for dep in service.depends_on:
                if dep in indegree:
                    indegree[service.name] += 1
        ready = [service.name for service in self.services if indegree[service.name] == 0]
        ordered = []
        while ready:
            name = ready.pop(0)
            ordered.append(by_name[name])
            for service in self.services:
                if name in service.depends_on:
                    indegree[service.name] -= 1
                    if indegree[service.name] == 0:
                        ready.append(service.name)
        if len(ordered) != len(self.services):
            raise ValueError(f"项目「{self.id}」的服务依赖存在循环，无法排序")
        return ordered


class Project:
    """一个项目的运行时对象：持有各服务的 ServiceProcess，并派生项目级状态。"""

    def __init__(self, spec: ProjectSpec, log_dir: str, on_log=None):
        """
        初始化项目。

        Args:
            spec: 项目规格。
            log_dir: 项目日志目录（每个服务一个 .log 文件）。
            on_log: 日志回调 `on_log(project_id, service_name, line)`。
        """
        self.spec = spec
        self.log_dir = log_dir
        self._processes = {}
        self._failure = ""
        self._lock = threading.Lock()
        for service_spec in spec.services:
            key = LogHub.make_key(spec.id, service_spec.name)
            callback = None
            if on_log is not None:
                callback = (
                    lambda line, pid=spec.id, name=service_spec.name: on_log(pid, name, line)
                )
            self._processes[service_spec.name] = ServiceProcess(
                service_spec,
                log_path=os.path.join(log_dir, f"{service_spec.name}.log"),
                on_line=callback,
            )

    @property
    def id(self) -> str:
        """项目标识。"""
        return self.spec.id

    def process(self, service_name: str):
        """
        获取指定服务的进程对象。

        Args:
            service_name: 服务名。

        Returns:
            ServiceProcess；不存在时返回 None。
        """
        return self._processes.get(service_name)

    def processes(self) -> list:
        """
        按配置顺序列出服务进程对象。

        Returns:
            ServiceProcess 列表。
        """
        return [self._processes[service.name] for service in self.spec.services]

    def ordered_processes(self) -> list:
        """
        按依赖拓扑序列出服务进程对象。

        Returns:
            ServiceProcess 列表（启动顺序）。
        """
        return [self._processes[service.name] for service in self.spec.topo_order()]

    def set_failure(self, message: str) -> None:
        """
        记录项目级失败原因（成功启动或停止时清空）。

        Args:
            message: 失败说明；空字符串表示清除。
        """
        with self._lock:
            self._failure = message or ""

    @property
    def failure(self) -> str:
        """
        项目级失败原因。

        编排线程记录原因之前，服务可能已经先进入 error（状态由服务状态派生），
        此时直接取该服务的说明，避免界面出现「红色卡片却没有原因」的瞬间。
        """
        with self._lock:
            failure = self._failure
        if failure:
            return failure
        for process in self.processes():
            if process.status == STATUS_ERROR and process.detail:
                return f"服务「{process.spec.name}」{process.detail}"
        return ""

    def status(self) -> str:
        """
        根据各服务状态派生项目状态。

        Returns:
            项目状态（running / starting / stopping / stopped / partial / error）。
        """
        statuses = [proc.status for proc in self.processes()]
        if not statuses:
            return PROJECT_STOPPED
        if any(status == STATUS_ERROR for status in statuses):
            return PROJECT_ERROR
        if all(status == STATUS_RUNNING for status in statuses):
            return PROJECT_RUNNING
        if all(status == STATUS_STOPPED for status in statuses):
            return PROJECT_STOPPED
        if any(status == STATUS_STOPPING for status in statuses):
            return PROJECT_STOPPING
        if any(status == STATUS_STARTING for status in statuses):
            return PROJECT_STARTING
        return PROJECT_PARTIAL

    def snapshot(self) -> dict:
        """
        生成项目状态快照（供前端渲染项目卡片）。

        - `icon` 已由 :func:`app.icons.resolve_icon` 解析：本地路径会被内联成 data URL
          （页面无法直接引用本地文件），解析失败则返回空字符串，前端回退首字母占位。
        - 每个服务带 `order`（拓扑排序后的启动序号，1 起）与 `dependsOn`；
          项目级另有 `startOrder`（服务名按启动顺序），供界面画出依赖链。

        Returns:
            包含项目元信息、派生状态与各服务状态的字典。
        """
        try:
            order_names = [process.spec.name for process in self.ordered_processes()]
        except ValueError:
            # 配置校验已拦截循环依赖，此处兜底为配置顺序
            order_names = [process.spec.name for process in self.processes()]
        position = {name: index + 1 for index, name in enumerate(order_names)}
        services = [proc.snapshot() for proc in self.processes()]
        for item in services:
            item["order"] = position.get(item["name"], 0)
        return {
            "id": self.spec.id,
            "name": self.spec.name,
            "icon": resolve_icon(self.spec.icon),
            "url": self.spec.url,
            "urlSource": self.spec.url_source,
            "autoOpen": self.spec.auto_open,
            "openIn": self.spec.open_in,
            "status": self.status(),
            "failure": self.failure,
            "startOrder": order_names,
            "services": services,
        }


class ProjectManager:
    """多项目管理器：启停编排、依赖顺序、状态快照与日志分发。"""

    def __init__(self, specs: list, log_dir: str, on_log=None):
        """
        初始化项目管理器。

        Args:
            specs: 项目规格列表。
            log_dir: 日志根目录（每个项目一个子目录）。
            on_log: 日志回调 `on_log(project_id, service_name, line)`。
        """
        self.log_dir = log_dir
        self._projects = {}
        # 正在编排的项目：{项目ID: 任务名}（start / stop / restart）
        self._busy = {}
        # 已请求取消启动的项目（编排线程在每个步骤之间检查）
        self._cancel = set()
        self._lock = threading.Lock()
        self._on_log = on_log
        for spec in specs:
            self._projects[spec.id] = Project(
                spec, os.path.join(log_dir, spec.id), on_log=on_log
            )

    def projects(self) -> list:
        """
        列出全部项目对象。

        Returns:
            Project 列表（按配置顺序）。
        """
        return list(self._projects.values())

    def get(self, project_id: str):
        """
        按 ID 获取项目。

        Args:
            project_id: 项目标识。

        Returns:
            Project；不存在时返回 None。
        """
        return self._projects.get(project_id)

    def state(self) -> dict:
        """
        生成全部项目的状态快照。

        Returns:
            {"projects": [项目快照, ...]}；每个项目额外带 `busy`（是否有编排在执行）
            与 `busyTask`（start / stop / restart），界面据此决定按钮可用性：
            「启动中」时「取消启动」应可用，而不是把按钮全置灰。
        """
        with self._lock:
            busy = dict(self._busy)
        snapshots = []
        for project in self.projects():
            item = project.snapshot()
            item["busy"] = project.id in busy
            item["busyTask"] = busy.get(project.id, "")
            snapshots.append(item)
        return {"projects": snapshots}

    def is_busy(self, project_id: str) -> bool:
        """
        判断项目是否正在执行启停编排。

        Args:
            project_id: 项目标识。

        Returns:
            True 表示有启停任务在执行。
        """
        with self._lock:
            return project_id in self._busy

    # ------------------------------------------------------------ 对外操作
    def start_project(self, project_id: str) -> dict:
        """
        启动项目（异步：立即返回，编排在工作线程中执行）。

        Args:
            project_id: 项目标识。

        Returns:
            {"ok": bool, "message": str}。
        """
        project = self._projects.get(project_id)
        if project is None:
            return {"ok": False, "message": f"项目不存在：{project_id}"}
        if not self._acquire(project_id, "start"):
            return {"ok": False, "message": f"项目「{project.spec.name}」正在执行启动/停止操作"}
        project.set_failure("")
        threading.Thread(
            target=self._start_sequence, args=(project,), name=f"start-{project_id}", daemon=True
        ).start()
        return {"ok": True, "message": f"正在启动项目「{project.spec.name}」"}

    def stop_project(self, project_id: str) -> dict:
        """
        停止项目（异步：按依赖逆序停止）。

        若该项目正在「启动/重启」过程中，本调用会转为**请求取消启动**：
        编排线程会在下一个检查点中止流程，并回滚已经启动的服务
        （否则遇到就绪超时很长的服务，用户只能干等）。

        Args:
            project_id: 项目标识。

        Returns:
            {"ok": bool, "message": str}。
        """
        project = self._projects.get(project_id)
        if project is None:
            return {"ok": False, "message": f"项目不存在：{project_id}"}
        with self._lock:
            current_task = self._busy.get(project_id, "")
        if current_task in ("start", "restart"):
            self._cancel.add(project_id)
            logging.info("收到取消请求：%s（%s）", project_id, project.spec.name)
            return {"ok": True, "message": f"正在取消「{project.spec.name}」的启动"}
        if not self._acquire(project_id, "stop"):
            return {"ok": False, "message": f"项目「{project.spec.name}」正在停止中"}
        project.set_failure("")
        threading.Thread(
            target=self._stop_sequence, args=(project,), name=f"stop-{project_id}", daemon=True
        ).start()
        return {"ok": True, "message": f"正在停止项目「{project.spec.name}」"}

    def restart_project(self, project_id: str) -> dict:
        """
        重启项目（异步：先按逆序停止，再按拓扑序启动）。

        Args:
            project_id: 项目标识。

        Returns:
            {"ok": bool, "message": str}。
        """
        project = self._projects.get(project_id)
        if project is None:
            return {"ok": False, "message": f"项目不存在：{project_id}"}
        if not self._acquire(project_id, "restart"):
            return {"ok": False, "message": f"项目「{project.spec.name}」正在执行启动/停止操作"}
        project.set_failure("")
        threading.Thread(
            target=self._restart_sequence, args=(project,), name=f"restart-{project_id}", daemon=True
        ).start()
        return {"ok": True, "message": f"正在重启项目「{project.spec.name}」"}

    def stop_all(self) -> None:
        """停止全部项目（按依赖逆序，同步执行；退出程序时调用）。"""
        for project in self.projects():
            for service in reversed(project.ordered_processes()):
                try:
                    service.stop()
                except Exception:
                    logging.exception("停止服务失败：%s/%s", project.id, service.spec.name)

    # ------------------------------------------------------------ 编排实现
    def _acquire(self, project_id: str, task: str = "") -> bool:
        """
        尝试占用项目的编排权（同一项目同时只允许一个启停任务）。

        Args:
            project_id: 项目标识。
            task: 任务名（start / stop / restart），用于界面展示与取消判定。

        Returns:
            True 表示占用成功。
        """
        with self._lock:
            if project_id in self._busy:
                return False
            self._busy[project_id] = task
            return True

    def _release(self, project_id: str) -> None:
        """释放项目的编排权。"""
        with self._lock:
            self._busy.pop(project_id, None)

    def _is_cancelled(self, project: Project) -> bool:
        """
        判断该项目是否被请求取消启动。

        Args:
            project: 目标项目。

        Returns:
            True 表示应中止启动流程。
        """
        return project.id in self._cancel

    def _wait_ready(self, project: Project, process: ServiceProcess, timeout: float) -> str:
        """
        等待服务就绪，期间持续响应取消请求（分片等待，避免取消后还要等满超时）。

        Args:
            project: 目标项目。
            process: 目标服务。
            timeout: 最长等待秒数。

        Returns:
            最终状态（running / error / stopped），或 CANCEL_STATE 表示已取消。
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self._is_cancelled(project):
                return CANCEL_STATE
            state = process.wait_settled(CANCEL_POLL_INTERVAL)
            if state in (STATUS_RUNNING, STATUS_ERROR, STATUS_STOPPED):
                return state
            if time.monotonic() >= deadline:
                return process.status

    def _cancel_start(self, project: Project, started: list) -> None:
        """
        执行「取消启动」：停掉本次已启动的服务，项目回到已停止。

        Args:
            project: 目标项目。
            started: 本次已成功启动的服务进程列表。
        """
        logging.info("启动已取消：%s（%s），正在回滚已启动的服务", project.id, project.spec.name)
        for process in reversed(started):
            try:
                process.stop()
            except Exception:
                logging.exception("取消启动时停止服务失败：%s", process.spec.name)
        project.set_failure("")

    def _start_sequence(self, project: Project) -> None:
        """
        启动编排：按拓扑序启动服务，前置就绪后再启动后继；全程可被取消。

        「取消」由 stop_project 置位，本线程在启动每个服务之前、以及等待就绪的过程中检查，
        因此长超时的服务也能被立刻中止，并回滚已经起来的服务。

        Args:
            project: 目标项目。
        """
        started = []
        self._cancel.discard(project.id)
        try:
            for process in project.ordered_processes():
                if self._is_cancelled(project):
                    self._cancel_start(project, started)
                    return
                failed_dep = self._wait_dependencies(project, process)
                if failed_dep == CANCEL_STATE:
                    self._cancel_start(project, started)
                    return
                if failed_dep:
                    self._fail(project, failed_dep, started)
                    return
                try:
                    process.start()
                except ServiceStartError as exc:
                    self._fail(project, str(exc), started)
                    return
                started.append(process)
                state = self._wait_ready(project, process, process.spec.ready_timeout + 5)
                if state == CANCEL_STATE:
                    self._cancel_start(project, started)
                    return
                if state != STATUS_RUNNING:
                    reason = process.detail or "服务未就绪"
                    self._fail(project, f"服务「{process.spec.name}」{reason}", started)
                    return
            project.set_failure("")
            logging.info("项目已启动：%s（%s）", project.id, project.spec.name)
        finally:
            self._cancel.discard(project.id)
            self._release(project.id)

    def _stop_sequence(self, project: Project) -> None:
        """
        停止编排：按依赖逆序停止全部服务。

        Args:
            project: 目标项目。
        """
        try:
            for process in reversed(project.ordered_processes()):
                try:
                    process.stop()
                except Exception as exc:
                    logging.exception("停止服务异常：%s", process.spec.name)
                    project.set_failure(f"停止服务「{process.spec.name}」时出错：{exc}")
            logging.info("项目已停止：%s（%s）", project.id, project.spec.name)
        finally:
            self._release(project.id)

    def _restart_sequence(self, project: Project) -> None:
        """
        重启编排：先按逆序停止，再按拓扑序启动。

        Args:
            project: 目标项目。
        """
        try:
            for process in reversed(project.ordered_processes()):
                try:
                    process.stop()
                except Exception:
                    logging.exception("停止服务异常：%s", process.spec.name)
            project.set_failure("")
        finally:
            self._release(project.id)
        self.start_project(project.id)

    def _wait_dependencies(self, project: Project, process: ServiceProcess):
        """
        等待某个服务的前置依赖就绪。

        Args:
            project: 目标项目。
            process: 待启动的服务。

        Returns:
            失败说明文本；全部依赖就绪时返回空字符串。
        """
        for dep_name in process.spec.depends_on:
            dep = project.process(dep_name)
            if dep is None:
                return f"服务「{process.spec.name}」依赖的服务不存在：{dep_name}"
            if dep.status == STATUS_RUNNING:
                continue
            state = self._wait_ready(project, dep, dep.spec.ready_timeout + 5)
            if state == CANCEL_STATE:
                return CANCEL_STATE
            if state != STATUS_RUNNING:
                return (
                    f"前置服务「{dep_name}」未就绪（当前状态：{state}"
                    f"{'，' + dep.detail if dep.detail else ''}）"
                    f"，已跳过启动「{process.spec.name}」"
                )
        return ""

    def _fail(self, project: Project, message: str, started: list) -> None:
        """
        处理启动失败：记录原因，必要时停止已启动的服务。

        Args:
            project: 目标项目。
            message: 失败说明。
            started: 本次已成功启动的服务进程列表。
        """
        project.set_failure(message)
        logging.error("项目启动失败：%s（%s）：%s", project.id, project.spec.name, message)
        if project.spec.stop_on_failure:
            for process in reversed(started):
                try:
                    process.stop()
                except Exception:
                    logging.exception("回滚停止服务失败：%s", process.spec.name)


def parse_projects(raw_projects) -> tuple:
    """
    解析并校验 `projects` 配置数组。

    校验项包括：ID 合法且唯一、服务名唯一、exe 为绝对路径且非脚本、
    `dependsOn` 指向存在且无环、就绪判定方式与端口/正则匹配等。

    Args:
        raw_projects: 配置中的 projects 值（期望为列表）。

    Returns:
        (ProjectSpec 列表, 问题描述列表)；问题列表非空时应拒绝启动多项目模式。
    """
    issues = []
    specs = []
    if raw_projects is None:
        return specs, issues
    if not isinstance(raw_projects, list):
        return specs, ["projects 必须是数组"]

    seen_ids = set()
    for index, raw in enumerate(raw_projects):
        label = f"第 {index + 1} 个项目"
        if not isinstance(raw, dict):
            issues.append(f"{label}：必须是 JSON 对象")
            continue
        project_id = raw.get("id")
        if not isinstance(project_id, str) or not project_id.strip():
            issues.append(f"{label}：id 不能为空")
            continue
        project_id = project_id.strip()
        if not _PROJECT_ID_PATTERN.match(project_id):
            issues.append(f"{label}（{project_id}）：id 只能包含字母、数字、下划线、点和短横线")
            continue
        if project_id in seen_ids:
            issues.append(f"{label}：id 重复（{project_id}）")
            continue
        seen_ids.add(project_id)

        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            issues.append(f"项目「{project_id}」：name 不能为空")
            continue

        url_source = raw.get("url_source", "fixed")
        if url_source not in ("fixed", "log"):
            issues.append(f"项目「{project_id}」：url_source 必须是 fixed 或 log")
            continue
        url_regex = raw.get("url_log_regex", r"(https?://\S+)")
        if url_source == "log":
            if not isinstance(url_regex, str) or not url_regex.strip():
                issues.append(f"项目「{project_id}」：url_source 为 log 时 url_log_regex 不能为空")
                continue
            try:
                re.compile(url_regex)
            except re.error:
                issues.append(f"项目「{project_id}」：url_log_regex 不是合法的正则表达式")
                continue

        raw_services = raw.get("services")
        if not isinstance(raw_services, list) or not raw_services:
            issues.append(f"项目「{project_id}」：services 必须是非空数组")
            continue

        open_in = raw.get("openIn", raw.get("open_in", "main"))
        if open_in not in ("main", "window", "browser"):
            issues.append(f"项目「{project_id}」：openIn 必须是 main / window / browser")
            continue

        service_specs, service_issues = _parse_services(project_id, raw_services)
        issues.extend(service_issues)
        if service_issues:
            continue

        specs.append(
            ProjectSpec(
                id=project_id,
                name=name.strip(),
                icon=raw.get("icon") if isinstance(raw.get("icon"), str) else "",
                url=raw.get("url").strip() if isinstance(raw.get("url"), str) else "",
                url_source=url_source,
                url_log_regex=url_regex if isinstance(url_regex, str) else r"(https?://\S+)",
                stop_on_failure=bool(raw.get("stop_on_failure", False)),
                auto_open=bool(raw.get("autoOpen", True)),
                open_in=open_in,
                services=service_specs,
            )
        )
    return specs, issues


def _parse_services(project_id: str, raw_services: list) -> tuple:
    """
    解析并校验单个项目的服务数组。

    Args:
        project_id: 所属项目 ID（用于错误信息）。
        raw_services: 服务配置数组。

    Returns:
        (ServiceSpec 列表, 问题描述列表)。
    """
    issues = []
    specs = []
    seen_names = set()
    for index, raw in enumerate(raw_services):
        label = f"项目「{project_id}」第 {index + 1} 个服务"
        if not isinstance(raw, dict):
            issues.append(f"{label}：必须是 JSON 对象")
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            issues.append(f"{label}：name 不能为空")
            continue
        name = name.strip()
        if name in seen_names:
            issues.append(f"{label}：服务名重复（{name}）")
            continue
        seen_names.add(name)

        exe = raw.get("exe")
        if not isinstance(exe, str) or not exe.strip():
            issues.append(f"{label}（{name}）：exe 不能为空")
            continue
        exe = exe.strip()
        if not os.path.isabs(exe):
            issues.append(f"{label}（{name}）：exe 必须是绝对路径（如 C:\\...\\python.exe）")
            continue
        if exe.lower().endswith((".cmd", ".bat", ".ps1")):
            issues.append(
                f"{label}（{name}）：exe 不能是 .cmd/.bat/.ps1 脚本，"
                "请改为直接指定解释器（python.exe / node.exe）加脚本参数"
            )
            continue

        args = raw.get("args", [])
        if not isinstance(args, list) or any(not isinstance(item, (str, int, float)) for item in args):
            issues.append(f"{label}（{name}）：args 必须是字符串数组")
            continue
        args = [str(item) for item in args]

        env = raw.get("env", {})
        if env is None:
            env = {}
        if not isinstance(env, dict):
            issues.append(f"{label}（{name}）：env 必须是对象（键值对）")
            continue

        port = raw.get("port", 0)
        if port is None:
            port = 0
        if isinstance(port, bool) or not isinstance(port, int) or not (0 <= port <= 65535):
            issues.append(f"{label}（{name}）：port 必须是 0~65535 的整数")
            continue

        health = raw.get("health", "")
        if health is None:
            health = ""
        if not isinstance(health, str):
            issues.append(f"{label}（{name}）：health 必须是字符串（如 /health）")
            continue

        ready_when = raw.get("readyWhen") or raw.get("ready_when")
        if not ready_when:
            ready_when = READY_HTTP if port else READY_PROCESS
        if ready_when not in READY_MODES:
            issues.append(
                f"{label}（{name}）：readyWhen 必须是 {'/'.join(READY_MODES)} 之一"
            )
            continue
        if ready_when == READY_HTTP and port <= 0:
            issues.append(f"{label}（{name}）：readyWhen 为 http 时必须配置 port")
            continue
        if ready_when == READY_LOG and not str(raw.get("readyRegex") or "").strip():
            issues.append(f"{label}（{name}）：readyWhen 为 log 时必须配置 readyRegex")
            continue

        ready_timeout = raw.get("readyTimeout", 60)
        if isinstance(ready_timeout, bool) or not isinstance(ready_timeout, (int, float)) or ready_timeout <= 0:
            issues.append(f"{label}（{name}）：readyTimeout 必须是大于 0 的数字")
            continue

        depends_on = raw.get("dependsOn", [])
        if depends_on is None:
            depends_on = []
        if not isinstance(depends_on, list) or any(not isinstance(item, str) for item in depends_on):
            issues.append(f"{label}（{name}）：dependsOn 必须是服务名数组")
            continue
        if name in depends_on:
            issues.append(f"{label}（{name}）：dependsOn 不能依赖自身")
            continue

        cwd = raw.get("cwd", "")
        if cwd is None:
            cwd = ""
        if not isinstance(cwd, str):
            issues.append(f"{label}（{name}）：cwd 必须是字符串路径")
            continue

        specs.append(
            ServiceSpec(
                name=name,
                exe=exe,
                args=args,
                cwd=cwd.strip(),
                env=env,
                port=port,
                health=health.strip(),
                ready_when=ready_when,
                ready_regex=str(raw.get("readyRegex") or ""),
                ready_timeout=float(ready_timeout),
                ready_delay=float(raw.get("readyDelay", 1.0) or 1.0),
                depends_on=list(depends_on),
                show_console=bool(raw.get("showConsole", False)),
                kill_on_exit=bool(raw.get("killOnExit", True)),
            )
        )

    # 依赖完整性：引用必须存在，且整体无环
    names = {spec.name for spec in specs}
    for spec in specs:
        for dep in spec.depends_on:
            if dep not in names:
                issues.append(f"项目「{project_id}」服务「{spec.name}」依赖的服务不存在：{dep}")
    if not issues:
        try:
            ProjectSpec(id=project_id, name=project_id, services=specs).topo_order()
        except ValueError as exc:
            issues.append(str(exc))
    return specs, issues
