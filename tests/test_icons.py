"""项目图标解析的单元测试：本地路径 → data URL、URL 透传、失败回退与缓存。"""

import base64
import os
import shutil
import sys
import unittest

from app.icons import MAX_ICON_BYTES, clear_cache, resolve_icon
from app.process import ServiceSpec
from app.projects import Project, ProjectSpec

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_TMP_ROOT = os.path.join(REPO_ROOT, ".tmp-tests")
PYTHON = sys.executable

# 1x1 透明 PNG（用于验证编码结果，不依赖任何外部文件）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class IconTests(unittest.TestCase):
    """resolve_icon 的行为契约。"""

    def setUp(self):
        clear_cache()
        self.work = os.path.join(TEST_TMP_ROOT, "icons")
        shutil.rmtree(self.work, ignore_errors=True)
        os.makedirs(self.work)

    def tearDown(self):
        clear_cache()
        shutil.rmtree(self.work, ignore_errors=True)

    def write_icon(self, name, data=PNG_1PX):
        """写一个图标文件并返回路径。"""
        path = os.path.join(self.work, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_empty_value_returns_empty(self):
        self.assertEqual(resolve_icon(""), "")
        self.assertEqual(resolve_icon("   "), "")
        self.assertEqual(resolve_icon(None), "")

    def test_remote_and_data_url_pass_through(self):
        for value in ("https://example.com/a.png", "http://127.0.0.1:8080/i.ico", "data:image/png;base64,AAAA"):
            self.assertEqual(resolve_icon(value), value)

    def test_local_png_becomes_data_url(self):
        path = self.write_icon("icon.png")
        result = resolve_icon(path)
        self.assertTrue(result.startswith("data:image/png;base64,"), result[:40])
        self.assertEqual(base64.b64decode(result.split(",", 1)[1]), PNG_1PX)

    def test_svg_and_ico_suffixes_are_supported(self):
        self.assertTrue(resolve_icon(self.write_icon("i.svg", b"<svg/>")).startswith("data:image/svg+xml;base64,"))
        self.assertTrue(resolve_icon(self.write_icon("i.ico", b"\x00\x00\x01\x00")).startswith("data:image/x-icon;base64,"))

    def test_missing_file_falls_back_to_empty(self):
        self.assertEqual(resolve_icon(os.path.join(self.work, "not-there.png")), "")

    def test_unsupported_suffix_falls_back_to_empty(self):
        self.assertEqual(resolve_icon(self.write_icon("icon.txt", b"hello")), "")

    def test_oversize_file_falls_back_to_empty(self):
        big = self.write_icon("big.png", b"\x89PNG" + b"\x00" * (MAX_ICON_BYTES + 10))
        self.assertEqual(resolve_icon(big), "")

    def test_cache_is_refreshed_when_file_changes(self):
        path = self.write_icon("icon.png")
        first = resolve_icon(path)
        # 内容与大小都变 → 缓存必须失效，返回新的 data URL
        self.write_icon("icon.png", PNG_1PX + b"\x00\x00\x00")
        second = resolve_icon(path)
        self.assertNotEqual(first, second, "文件变化后应重新读取，而不是返回缓存")
        # 内容未变时返回同一结果（命中缓存）
        self.assertEqual(second, resolve_icon(path))

    def test_project_snapshot_carries_resolved_icon(self):
        icon_path = self.write_icon("proj.png")
        spec = ProjectSpec(
            id="p",
            name="带图标的项目",
            icon=icon_path,
            services=[ServiceSpec(name="s", exe=PYTHON, args=["-c", "print('x')"], ready_when="process")],
        )
        project = Project(spec, log_dir=self.work)
        snapshot = project.snapshot()
        self.assertTrue(snapshot["icon"].startswith("data:image/png;base64,"))
        # 未配置图标 → 空字符串，前端回退首字母
        spec.icon = ""
        self.assertEqual(Project(spec, log_dir=self.work).snapshot()["icon"], "")


if __name__ == "__main__":
    unittest.main()
