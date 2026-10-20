"""项目图标解析：把配置里的 icon 变成前端可以直接使用的值。

为什么需要这一层：
- 启动器页面是用 `webview.create_window(html=...)` 注入的，页面来源不是文件路径，
  浏览器不允许它直接加载 `C:\...\icon.png` 这类本地路径（会被当成跨源/非法请求）。
- 因此本地图标由 Python 读成 `data:` URL 再交给页面；http(s) 与 data: 则原样透传。
- 状态快照每秒钟会被推送多次，读盘必须带缓存（按 路径+mtime+大小 失效），否则会反复读同一个文件。

失败一律返回空字符串 —— 前端会回退到「项目名首字母」的占位图标，不影响使用。
"""

import base64
import logging
import os
import threading

# 单个图标文件的大小上限（data URL 会内联进状态快照，过大既拖慢推送也没必要）
MAX_ICON_BYTES = 512 * 1024

# 允许的图片后缀 → MIME 类型
_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}

# 缓存：路径 -> (mtime, size, data_url)
_cache = {}
_cache_lock = threading.Lock()


def resolve_icon(icon: str) -> str:
    """
    解析配置项 `icon`：

    - 空值 → 空字符串（前端显示首字母占位）；
    - `http://` / `https://` / `data:` → 原样返回；
    - 其它视为本地文件路径 → 读取并编码为 `data:` URL；文件不存在、后缀不支持或过大时返回空字符串。

    Args:
        icon: 配置里的图标值。

    Returns:
        页面可直接使用的图标值（URL 或 data URL），无法使用时为空字符串。
    """
    value = (icon or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "data:")):
        return value
    return _local_icon_data_url(value)


def _local_icon_data_url(path: str) -> str:
    """
    读取本地图标文件并编码为 data URL（带 mtime+size 缓存）。

    Args:
        path: 图标文件路径（支持 ~ 开头）。

    Returns:
        data URL；不可用时为空字符串。
    """
    expanded = os.path.expanduser(path)
    suffix = os.path.splitext(expanded)[1].lower()
    mime = _MIME_BY_SUFFIX.get(suffix)
    if mime is None:
        logging.warning("图标后缀不支持，已回退首字母占位：%s", path)
        return ""
    try:
        stat = os.stat(expanded)
    except OSError:
        logging.warning("图标文件不存在或不可读，已回退首字母占位：%s", path)
        return ""
    if stat.st_size > MAX_ICON_BYTES:
        logging.warning(
            "图标文件过大（%s 字节 > %s），已回退首字母占位：%s",
            stat.st_size,
            MAX_ICON_BYTES,
            path,
        )
        return ""

    with _cache_lock:
        cached = _cache.get(expanded)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]

    try:
        with open(expanded, "rb") as handle:
            raw = handle.read(MAX_ICON_BYTES + 1)
    except OSError as exc:
        logging.warning("读取图标失败（%s），已回退首字母占位：%s", exc, path)
        return ""
    if len(raw) > MAX_ICON_BYTES:
        logging.warning("图标文件过大，已回退首字母占位：%s", path)
        return ""

    data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    with _cache_lock:
        _cache[expanded] = (stat.st_mtime, stat.st_size, data_url)
    return data_url


def clear_cache() -> None:
    """清空图标缓存（配置重新加载或测试时使用）。"""
    with _cache_lock:
        _cache.clear()
