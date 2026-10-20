"""路径工具：兼容源码运行与 PyInstaller 打包（frozen）两种场景，并提供基于环境变量的家目录解析。"""

import os
import sys


def get_app_dir() -> str:
    """
    获取应用根目录。

    - 打包（frozen）后：可执行文件（exe）所在目录（配置目录见 app.config.get_config_dir）。
    - 源码运行时：项目根目录。

    Returns:
        应用根目录绝对路径。
    """
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后 sys.executable 指向 exe 本身
        return os.path.dirname(os.path.abspath(sys.executable))
    # 源码运行时：本文件位于 <项目根>/app/ 下，向上取一级
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_home_dir() -> str:
    """
    获取当前用户家目录（从环境变量读取）。

    - Windows：优先读取 USERPROFILE；缺失时用 HOMEDRIVE（如 "C:"）与 HOMEPATH（如 "\\Users\\xxx"）拼接。
    - 其他系统：读取 HOME。
    - 以上环境变量均缺失时退化为 os.path.expanduser("~")，避免程序无法启动。

    Returns:
        用户家目录绝对路径。
    """
    if os.name == "nt":
        home = os.environ.get("USERPROFILE")
        if not home:
            drive = os.environ.get("HOMEDRIVE") or ""
            home_path = os.environ.get("HOMEPATH") or ""
            home = (drive + home_path) or None
    else:
        home = os.environ.get("HOME")
    if not home:
        home = os.path.expanduser("~")
    return os.path.abspath(home)


def expand_home_path(path: str) -> str:
    """
    将路径开头的 ~ 展开为用户家目录（家目录从环境变量获取，见 get_home_dir）。

    - "~"、"~/xxx"、"~\\xxx" 中的 ~ 会被替换为家目录
    - 其他路径（如 "~user/xxx"）原样返回

    Args:
        path: 待展开的路径字符串。

    Returns:
        展开后的路径字符串。
    """
    if not path:
        return path
    home = get_home_dir()
    if path == "~":
        return home
    if path.startswith("~/"):
        return os.path.join(home, path[2:])
    if path.startswith("~\\"):
        return os.path.join(home, path[2:])
    return path
