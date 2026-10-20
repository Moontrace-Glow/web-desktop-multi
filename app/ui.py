"""UI 层：创建 pywebview 窗口（基于系统 Edge/WebView2 内核），并向页面暴露 Python 接口。"""

import logging

import webview

from app.ui_state import load_toolbar_pos, save_toolbar_pos


class BridgeApi:
    """暴露给页面 JavaScript 调用的接口（配置页与错误页按钮、齿轮按钮位置持久化）。"""

    def __init__(self, controller):
        """
        初始化接口。

        Args:
            controller: 应用控制器实例。
        """
        self._controller = controller

    def retry(self) -> None:
        """页面「重试」按钮回调：重新执行服务启动流程。"""
        self._controller.retry()

    def exit_app(self) -> None:
        """页面「退出」按钮回调：关闭窗口。"""
        self._controller.exit_app()

    def save_config(self, data: dict) -> dict:
        """
        配置页「保存」按钮回调：校验并保存配置。

        Args:
            data: 页面提交的表单数据。

        Returns:
            {"ok": bool, "message": str} 结果字典。
        """
        return self._controller.save_config(data)

    def restart_app(self) -> None:
        """配置页保存成功后延时调用：重启应用。"""
        self._controller.restart_app()

    def open_config_page(self, source: str = "web") -> None:
        """
        打开配置页面（遮罩式，右上角带关闭按钮）。

        Args:
            source: 打开来源。"web" 表示从目标网页齿轮按钮进入（保持服务运行，
                关闭配置页后返回目标网页）；"error" 表示从错误页进入
                （先停止残留服务，关闭配置页后返回错误页）。
        """
        self._controller.open_config_page(source)

    def exit_config_page(self) -> None:
        """配置页右上角「✕」按钮回调：关闭配置页并返回来源页面。"""
        self._controller.exit_config_page()

    def get_toolbar_pos(self):
        """
        读取齿轮按钮位置（供页面脚本查询）。

        Returns:
            {"left": float, "top": float, "vw": float, "vh": float}；无记录时返回 None。
        """
        return load_toolbar_pos()

    def save_toolbar_pos(self, left, top, view_width=0, view_height=0) -> dict:
        """
        保存齿轮按钮位置（页面拖动结束时调用，写入 Python 侧状态文件）。

        Args:
            left: 图标左边缘位置（CSS 像素）。
            top: 图标上边缘位置（CSS 像素）。
            view_width: 当前视口宽度（CSS 像素）。
            view_height: 当前视口高度（CSS 像素）。

        Returns:
            {"ok": bool} 结果字典。
        """
        return {"ok": save_toolbar_pos(left, top, view_width, view_height)}


def create_main_window(controller, config: dict, html: str):
    """
    创建主窗口：初始页面由调用方指定（等待页或配置页）。

    Args:
        controller: 应用控制器实例（绑定窗口与关闭事件）。
        config: 应用配置字典。
        html: 初始页面 HTML。

    Returns:
        pywebview 窗口对象。
    """
    size = config.get("window_size", [1200, 800])
    window = webview.create_window(
        title=config.get("window_title", "Web 桌面启动器"),
        html=html,
        js_api=BridgeApi(controller),
        width=int(size[0]),
        height=int(size[1]),
        min_size=(800, 600),
        text_select=True,
        # 窗口底色会作为内核 DefaultBackgroundColor：深色值可能让内核推断为深色模式
        # （右键菜单等原生 UI 变深色，WebView2 已知问题），故使用浅色
        background_color="#ffffff",
    )
    controller.set_window(window)
    logging.info("主窗口已创建：%s（%sx%s）", window.title, size[0], size[1])
    return window
