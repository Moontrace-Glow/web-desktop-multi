"""配置模块：管理 ~/.WebDesktop/config.json 的创建、加载、校验与保存。"""

import copy
import json
import logging
import os
import re

from app.paths import expand_home_path, get_app_dir, get_home_dir

# 配置目录名：位于用户家目录下（Windows 上即 C:\Users\<用户名>\.WebDesktop）
CONFIG_DIR_NAME = ".WebDesktop"
CONFIG_FILE_NAME = "config.json"

# 便携/测试模式：设置该环境变量后，配置目录改用它（默认值里的 ~/.WebDesktop 前缀一并改写）
HOME_OVERRIDE_ENV = "WEBDESKTOP_HOME"
_DEFAULT_HOME_TOKEN = "~/.WebDesktop"

# 便携模式：程序目录下存在该标记文件时，配置与日志都写在「程序目录/data」下。
# 适用场景：U 盘部署、绿色免安装、以及不允许写用户目录的受限环境
# （例如可执行文件位于带低完整性标签的目录时，它写不了用户目录）。
PORTABLE_MARKER = "portable.txt"
PORTABLE_DATA_DIR = "data"

# 关闭窗口动作的可选值：最小化到系统托盘 / 退出程序
CLOSE_ACTION_MINIMIZE_TO_TRAY = "minimize_to_tray"
CLOSE_ACTION_EXIT = "exit"
# 关闭窗口动作的默认值（首次运行时生效）：最小化到系统托盘
DEFAULT_CLOSE_ACTION = CLOSE_ACTION_MINIMIZE_TO_TRAY

# 默认配置：除 web_command / web_url 外所有参数均有默认值；
# web_command 与 web_url 为必填项但无默认值，留空时程序进入配置页面引导用户填写
DEFAULT_CONFIG = {
    # 服务启动命令：完整命令行字符串，含空格的路径请用双引号包裹（必填，无默认值）
    "web_command": "",
    # 服务地址：服务就绪后跳转的地址（必填，无默认值）
    "web_url": "",
    # 访问地址来源：fixed=直接使用 web_url 固定地址 / log=服务就绪后用正则从服务日志提取
    "url_source": "fixed",
    # 从服务日志提取访问地址的正则（url_source 为 log 时生效）
    "url_log_regex": r"(https?://\S+)",
    # 服务工作目录：默认 ~/.WebDesktop/working
    "working_dir": "~/.WebDesktop/working",
    # 等待服务就绪的超时秒数
    "startup_timeout": 60,
    # 健康检查轮询间隔（秒）
    "check_interval": 0.5,
    # 单次健康检查超时（秒）
    "check_timeout": 2,
    # 窗口标题
    "window_title": "WebDesktop",
    # 窗口尺寸 [宽, 高]
    "window_size": [1200, 800],
    # 是否显示服务控制台窗口（调试用）
    "show_console": False,
    "private_mode": False,
    # 退出程序时是否终止服务进程（最小化到系统托盘期间服务不受影响）
    "kill_on_exit": True,
    # 关闭窗口动作：minimize_to_tray 最小化到系统托盘 / exit 退出程序
    "close_action": DEFAULT_CLOSE_ACTION,
    # 日志目录：默认 ~/.WebDesktop/log
    "log_dir": "~/.WebDesktop/log",
    # 多项目模式：非空时启用多项目启动器（每个元素见 app.projects.parse_projects）
    # 为空时沿用单服务模式（web_command + web_url），保持向后兼容
    "projects": [],
}

# 配置项的中文名称（校验提示信息用）
_CONFIG_LABELS = {
    "web_command": "服务启动命令",
    "web_url": "服务地址",
    "url_source": "访问地址来源",
    "url_log_regex": "日志提取正则",
    "working_dir": "工作目录",
    "log_dir": "日志目录",
    "window_title": "窗口标题",
}


def get_portable_data_dir() -> str:
    """
    检测便携模式：程序目录下存在 `portable.txt` 时返回「程序目录/data」。

    便携模式让配置、日志、工作目录全部落在程序自己旁边，不再碰用户家目录，
    因此在 U 盘部署、绿色免安装、以及不允许写用户目录的受限环境里都能直接运行。

    Returns:
        便携数据目录绝对路径；非便携模式返回空字符串。
    """
    try:
        app_dir = get_app_dir()
    except Exception:
        return ""
    if os.path.isfile(os.path.join(app_dir, PORTABLE_MARKER)):
        return os.path.join(app_dir, PORTABLE_DATA_DIR)
    return ""


def _default_base_dir() -> str:
    """
    返回「默认值里的 ~/.WebDesktop 应改写到哪个目录」。

    Returns:
        覆盖目录（WEBDESKTOP_HOME 或便携数据目录）；无需改写时返回空字符串。
    """
    override = os.environ.get(HOME_OVERRIDE_ENV, "").strip()
    if override:
        return os.path.abspath(expand_home_path(override))
    return get_portable_data_dir()


def get_config_dir() -> str:
    """
    获取配置目录的绝对路径。

    优先级：`WEBDESKTOP_HOME` 环境变量 → 便携模式（程序目录下有 portable.txt）→
    默认 `~/.WebDesktop`（~ 为用户家目录，从环境变量获取）。

    Returns:
        配置目录绝对路径。
    """
    base = _default_base_dir()
    if base:
        return base
    return os.path.join(get_home_dir(), CONFIG_DIR_NAME)


def get_config_path() -> str:
    """
    获取配置文件（~/.WebDesktop/config.json）的绝对路径。

    Returns:
        配置文件绝对路径。
    """
    return os.path.join(get_config_dir(), CONFIG_FILE_NAME)


def ensure_config_file() -> str:
    """
    确保配置目录与配置文件存在：目录不存在时创建目录，配置文件不存在时用默认配置创建。

    Returns:
        配置文件绝对路径。
    """
    config_dir = get_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    path = get_config_path()
    if not os.path.exists(path):
        write_config(copy.deepcopy(DEFAULT_CONFIG))
        logging.info("配置文件不存在，已使用默认配置创建：%s", path)
    return path


def load_config() -> dict:
    """
    加载配置：确保配置文件存在，与默认配置合并并规范化路径。

    Returns:
        合并后的完整配置字典（不保证合法，完整性由 get_config_issues 检查）。
    """
    path = ensure_config_file()
    config = copy.deepcopy(DEFAULT_CONFIG)
    user_config = _read_config(path)
    if user_config is not None:
        _merge(config, user_config)
    else:
        logging.warning("配置文件读取失败，将使用默认配置：%s", path)
    _remap_home_override(config)
    _normalize_paths(config)
    return config


def _remap_home_override(config: dict) -> None:
    """
    把默认目录值中的 `~/.WebDesktop` 前缀改写到「实际使用的配置目录」。

    默认值里 `working_dir` / `log_dir` 写的是 `~/.WebDesktop/...` 字面量，
    若不改写，便携模式或 WEBDESKTOP_HOME 下会出现「配置目录变了、日志与工作目录
    却仍落在用户家目录」的错位。

    Args:
        config: 配置字典（就地修改）。
    """
    base = _default_base_dir()
    if not base:
        return
    token = _DEFAULT_HOME_TOKEN.lower()
    for key in ("working_dir", "log_dir"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = value.strip().replace("\\", "/")
        lowered = normalized.lower()
        if lowered == token:
            config[key] = base
        elif lowered.startswith(token + "/"):
            config[key] = os.path.join(base, normalized[len(token) + 1:])


def get_config_issues(config: dict) -> list:
    """
    检查配置完整性：任一参数为空或取值不合法都会记录问题。

    Args:
        config: 合并后的配置字典。

    Returns:
        问题描述列表；空列表表示配置可用。
    """
    issues = []

    command = config.get("web_command")
    if not isinstance(command, str) or not command.strip():
        issues.append(f"{_CONFIG_LABELS['web_command']}（web_command）不能为空")

    url = config.get("web_url")
    if not isinstance(url, str) or not url.strip():
        issues.append(f"{_CONFIG_LABELS['web_url']}（web_url）不能为空")
    elif not url.startswith(("http://", "https://")):
        issues.append(f"{_CONFIG_LABELS['web_url']}（web_url）必须以 http:// 或 https:// 开头")

    url_source = config.get("url_source")
    if url_source not in ("fixed", "log"):
        issues.append(f"{_CONFIG_LABELS['url_source']}（url_source）必须是 fixed（固定地址）或 log（从日志提取）")

    url_regex = config.get("url_log_regex")
    if url_source == "log":
        if not isinstance(url_regex, str) or not url_regex.strip():
            issues.append(f"{_CONFIG_LABELS['url_log_regex']}（url_log_regex）不能为空")
        elif not _is_valid_regex(url_regex):
            issues.append(f"{_CONFIG_LABELS['url_log_regex']}（url_log_regex）不是合法的正则表达式")

    for key in ("working_dir", "log_dir"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{_CONFIG_LABELS[key]}（{key}）不能为空")

    for key in ("startup_timeout", "check_interval", "check_timeout"):
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            issues.append(f"{key} 必须是大于 0 的数字")

    title = config.get("window_title")
    if not isinstance(title, str) or not title.strip():
        issues.append(f"{_CONFIG_LABELS['window_title']}（window_title）不能为空")

    size = config.get("window_size")
    valid_size = (
        isinstance(size, list)
        and len(size) == 2
        and all(
            not isinstance(item, bool) and isinstance(item, (int, float)) and item > 0
            for item in size
        )
    )
    if not valid_size:
        issues.append("window_size（窗口尺寸）必须是 [宽, 高] 两个正数")

    for key in ("show_console", "kill_on_exit", "private_mode"):
        if not isinstance(config.get(key), bool):
            issues.append(f"{key} 必须是布尔值（true/false）")

    close_action = config.get("close_action")
    if close_action not in (CLOSE_ACTION_MINIMIZE_TO_TRAY, CLOSE_ACTION_EXIT):
        issues.append(f"close_action（关闭窗口动作）必须是 {CLOSE_ACTION_MINIMIZE_TO_TRAY} 或 {CLOSE_ACTION_EXIT}")

    return issues


def build_config_from_form(data: dict):
    """
    将配置页面提交的表单数据转换为配置字典，并做完整性校验（全部参数必填）。

    Args:
        data: 页面提交的原始数据（字符串与布尔值混合）。

    Returns:
        (config, error) 元组：校验失败时 config 为空字典、error 为错误描述；
        校验通过时 error 为空字符串。
    """
    if not isinstance(data, dict):
        return {}, "提交的数据格式不正确"

    config = {}

    # 必填字符串字段：去除首尾空白后不能为空
    command = _text(data, "web_command")
    if not command:
        return {}, f"{_CONFIG_LABELS['web_command']}（web_command）不能为空"
    config["web_command"] = command

    url = _text(data, "web_url")
    if not url:
        return {}, f"{_CONFIG_LABELS['web_url']}（web_url）不能为空"
    if not url.startswith(("http://", "https://")):
        return {}, f"{_CONFIG_LABELS['web_url']}（web_url）必须以 http:// 或 https:// 开头"
    config["web_url"] = url

    # 访问地址来源：fixed=固定地址 / log=从服务日志用正则提取
    url_source = data.get("url_source")
    if url_source not in ("fixed", "log"):
        return {}, f"{_CONFIG_LABELS['url_source']}（url_source）必须是 fixed（固定地址）或 log（从日志提取）"
    config["url_source"] = url_source

    # 日志提取正则：仅 url_source 为 log 时必填且需是合法正则
    url_regex = _text(data, "url_log_regex")
    if url_source == "log":
        if not url_regex:
            return {}, f"{_CONFIG_LABELS['url_log_regex']}（url_log_regex）不能为空"
        if not _is_valid_regex(url_regex):
            return {}, f"{_CONFIG_LABELS['url_log_regex']}（url_log_regex）不是合法的正则表达式"
    config["url_log_regex"] = url_regex

    # 目录字段：必填，写入前展开 ~ 并转为绝对路径
    for key in ("working_dir", "log_dir"):
        value = _text(data, key)
        if not value:
            return {}, f"{_CONFIG_LABELS[key]}（{key}）不能为空"
        config[key] = _expand_path(value)

    # 数字字段：必填且大于 0
    for key in ("startup_timeout", "check_interval", "check_timeout"):
        number = _parse_positive_number(data.get(key))
        if number is None:
            return {}, f"{key} 必须是大于 0 的数字"
        config[key] = number

    title = _text(data, "window_title")
    if not title:
        return {}, f"{_CONFIG_LABELS['window_title']}（window_title）不能为空"
    config["window_title"] = title

    # 窗口尺寸：必填且宽高均为正数（转为整数保存）
    size = data.get("window_size")
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        return {}, "window_size（窗口尺寸）必须是 [宽, 高] 两个正数"
    width = _parse_positive_number(size[0])
    height = _parse_positive_number(size[1])
    if width is None or height is None or int(width) <= 0 or int(height) <= 0:
        return {}, "window_size（窗口尺寸）必须是 [宽, 高] 两个正数"
    config["window_size"] = [int(width), int(height)]

    # 布尔字段：接受布尔值与 "true"/"false" 字符串
    for key in ("show_console", "kill_on_exit", "private_mode"):
        boolean = _parse_bool(data.get(key))
        if boolean is None:
            return {}, f"{key} 必须是布尔值（true/false）"
        config[key] = boolean

    # 关闭窗口动作：单选枚举值
    close_action = data.get("close_action")
    if close_action not in (CLOSE_ACTION_MINIMIZE_TO_TRAY, CLOSE_ACTION_EXIT):
        return {}, f"close_action（关闭窗口动作）必须是 {CLOSE_ACTION_MINIMIZE_TO_TRAY} 或 {CLOSE_ACTION_EXIT}"
    config["close_action"] = close_action

    return config, ""


def write_config(config: dict) -> None:
    """
    将配置写入 ~/.WebDesktop/config.json（先写临时文件再替换，避免写入中断导致文件损坏）。

    Args:
        config: 待写入的配置字典。
    """
    config_dir = get_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    path = get_config_path()
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)
    logging.info("配置已写入：%s", path)


def _read_config(path: str):
    """
    读取指定路径的 JSON 配置文件。

    用 `utf-8-sig` 读取：记事本、部分编辑器/脚本写 JSON 时会带 UTF-8 BOM，
    若按 utf-8 解析会直接抛错，表现为「配置好像没生效、界面回到默认状态」。

    Args:
        path: 配置文件路径。

    Returns:
        解析出的字典；文件不存在或解析失败时返回 None。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("配置文件根节点必须是 JSON 对象")
        return data
    except FileNotFoundError:
        logging.info("配置文件不存在：%s", path)
        return None
    except (OSError, ValueError) as exc:
        logging.error("读取配置文件失败：%s，原因：%s", path, exc)
        return None


def _merge(config: dict, user_config: dict) -> None:
    """将用户配置合并进默认配置：缺失字段保留默认值，字符串字段做类型兜底。"""
    for key in DEFAULT_CONFIG:
        if key in user_config and user_config[key] is not None:
            config[key] = user_config[key]
    # 字符串字段类型异常时置空，交由完整性检查提示用户
    for key in ("web_command", "web_url", "url_log_regex", "working_dir", "window_title", "log_dir"):
        if not isinstance(config[key], str):
            config[key] = ""
    # url_source 类型异常时回退为默认值
    if not isinstance(config.get("url_source"), str):
        config["url_source"] = DEFAULT_CONFIG["url_source"]


def _normalize_paths(config: dict) -> None:
    """规范化目录字段：展开 ~ 并转为绝对路径（相对路径以配置目录为基准）。"""
    base = get_config_dir()
    for key in ("working_dir", "log_dir"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            config[key] = _expand_path(value, base)


def _expand_path(value: str, base: str = "") -> str:
    """展开 ~（家目录从环境变量获取）并将相对路径转换为绝对路径。base 为空时以配置目录为基准。"""
    expanded = expand_home_path(value.strip())
    if not os.path.isabs(expanded):
        expanded = os.path.join(base or get_config_dir(), expanded)
    return os.path.abspath(expanded)


def _text(data: dict, key: str) -> str:
    """读取表单字符串字段并去除首尾空白。"""
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _parse_positive_number(value):
    """解析大于 0 的数字，解析失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if number > 0 else None


def _parse_bool(value):
    """解析布尔值（接受布尔与 "true"/"false" 字符串），解析失败返回 None。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def _is_valid_regex(pattern: str) -> bool:
    """判断字符串是否为合法的正则表达式（可成功编译）。"""
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False
