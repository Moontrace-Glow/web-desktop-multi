"""界面状态存储：保存与加载与网页无关、仅属于启动器自身的界面状态。

当前保存的目标网页浮动齿轮按钮位置（默认停在右下角，可拖动）：
写入 `~/.WebDesktop/ui_state.json`，与 config.json 分离——配置页保存时会整体
重建 config.json，界面状态放进去会被覆盖。

写入采用「先写临时文件再替换」的原子方式，避免写入中断导致文件损坏；
读取失败 / 内容不合法时一律返回 None，由调用方回退到默认位置。
"""

import json
import logging
import math
import os
import threading
import time

from app.config import get_config_dir

# 界面状态文件名（位于配置目录 ~/.WebDesktop 下）
UI_STATE_FILE_NAME = "ui_state.json"

# 原子替换失败时的重试次数与间隔（Windows 上目标文件偶发被占用）
REPLACE_RETRIES = 3
REPLACE_RETRY_DELAY = 0.15

# 写锁：页面 js 桥的每次调用都在独立线程执行（可能并发到达），
# 必须串行化「读取现有状态 → 合并 → 写文件」，否则会出现替换临时文件时文件已消失
# （WinError 2）这类互相踩踏的问题。
_WRITE_LOCK = threading.Lock()

# 状态结构中的字段名
TOOLBAR_POS_KEY = "toolbar_pos"
FIELD_LEFT = "left"
FIELD_TOP = "top"
FIELD_VIEW_WIDTH = "vw"
FIELD_VIEW_HEIGHT = "vh"


def get_ui_state_path() -> str:
    """
    获取界面状态文件（ui_state.json）的绝对路径。

    Returns:
        状态文件绝对路径。
    """
    return os.path.join(get_config_dir(), UI_STATE_FILE_NAME)


def load_ui_state() -> dict:
    """
    读取界面状态。

    Returns:
        状态字典；文件不存在、解析失败或根节点不是对象时返回空字典。
    """
    path = get_ui_state_path()
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        # 首次运行属正常情况：无状态，直接使用默认值
        return {}
    except (OSError, ValueError) as exc:
        logging.warning("读取界面状态失败（将使用默认值）：%s，原因：%s", path, exc)
        return {}
    if not isinstance(data, dict):
        logging.warning("界面状态文件根节点不是 JSON 对象，已忽略：%s", path)
        return {}
    return data


def load_toolbar_pos():
    """
    读取已保存的齿轮按钮位置。

    Returns:
        {"left": float, "top": float, "vw": float, "vh": float}；无有效记录时返回 None。
    """
    state = load_ui_state()
    pos = state.get(TOOLBAR_POS_KEY)
    if not isinstance(pos, dict):
        return None
    left = _positive_or_zero_number(pos.get(FIELD_LEFT))
    top = _positive_or_zero_number(pos.get(FIELD_TOP))
    if left is None or top is None:
        return None
    return {
        FIELD_LEFT: left,
        FIELD_TOP: top,
        # 视口尺寸用于窗口大小变化时换算位置；缺失时按 0 处理，由页面侧回退为当前视口
        FIELD_VIEW_WIDTH: _positive_or_zero_number(pos.get(FIELD_VIEW_WIDTH)) or 0.0,
        FIELD_VIEW_HEIGHT: _positive_or_zero_number(pos.get(FIELD_VIEW_HEIGHT)) or 0.0,
    }


def save_toolbar_pos(left, top, view_width=0, view_height=0) -> bool:
    """
    保存齿轮按钮位置（其余已有状态字段保持不变）。

    Args:
        left: 图标左边缘位置（CSS 像素）。
        top: 图标上边缘位置（CSS 像素）。
        view_width: 记录时的视口宽度（CSS 像素）。
        view_height: 记录时的视口高度（CSS 像素）。

    Returns:
        校验并写入成功返回 True，参数非法或写入失败返回 False。
    """
    left_value = _finite_number(left)
    top_value = _finite_number(top)
    if left_value is None or top_value is None:
        logging.warning("忽略非法的齿轮位置：left=%r top=%r", left, top)
        return False

    # 加锁：并发写会互相覆盖或抢占临时文件，必须串行执行
    with _WRITE_LOCK:
        state = load_ui_state()
        state[TOOLBAR_POS_KEY] = {
            FIELD_LEFT: max(0.0, left_value),
            FIELD_TOP: max(0.0, top_value),
            FIELD_VIEW_WIDTH: max(0.0, _finite_number(view_width) or 0.0),
            FIELD_VIEW_HEIGHT: max(0.0, _finite_number(view_height) or 0.0),
        }
        return _write_ui_state(state)


def _write_ui_state(state: dict) -> bool:
    """
    将界面状态写入磁盘（先写临时文件再原子替换）。

    Windows 上目标文件可能被索引、杀毒或映射该文件的进程短暂占用，
    此时 `os.replace` 会抛 PermissionError；因此重试若干次，仍失败再退化为
    直接覆盖写入（界面状态不是关键数据，可接受非原子写入）。

    Args:
        state: 待写入的状态字典。

    Returns:
        写入成功返回 True，失败返回 False。
    """
    path = get_ui_state_path()
    temp_path = path + ".tmp"
    content = json.dumps(state, ensure_ascii=False, indent=2)
    try:
        os.makedirs(get_config_dir(), exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as file:
            file.write(content)
    except OSError:
        logging.exception("写入界面状态临时文件失败：%s", temp_path)
        return False

    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(temp_path, path)
            return True
        except OSError as exc:
            if attempt < REPLACE_RETRIES - 1:
                # 短暂被占用：等待后重试
                time.sleep(REPLACE_RETRY_DELAY)
                continue
            logging.warning("替换界面状态文件失败（%s），改为直接覆盖写入：%s", exc, path)

    try:
        with open(path, "w", encoding="utf-8") as file:
            file.write(content)
        # 兜底成功：清掉可能残留的临时文件
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return True
    except OSError:
        logging.exception("写入界面状态失败：%s", path)
        return False


def _finite_number(value):
    """
    将入参转换为有限数值。

    Args:
        value: 任意入参（可能来自页面 js 调用）。

    Returns:
        有限 float；非数值、布尔值、无穷大或 NaN 时返回 None。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_or_zero_number(value):
    """
    将已存储的字段转换为非负有限数值（读取路径的宽松校验）。

    Args:
        value: 存储文件中的字段值。

    Returns:
        非负 float；非法时返回 None。
    """
    number = _finite_number(value)
    if number is None or number < 0:
        return None
    return number
