import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import main
from app.config import DEFAULT_CONFIG


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.enterContext(patch('main.load_config', return_value=self.config))
        self.enterContext(patch('main.setup_logging'))
        self.enterContext(patch('main.create_main_window'))
        # 单实例互斥独立测试（SingleInstanceTests），此处放行避免影响既有 main() 用例
        self.enterContext(patch('main.acquire_single_instance', return_value=True))
        self.controller = self.enterContext(patch('main.AppController')).return_value
        self.tray = self.enterContext(patch('main.SystemTray')).return_value
        self.start = self.enterContext(patch('main.webview.start'))
        self.wait = self.enterContext(patch('main.wait_for_browser_exit'))
        self.fatal = self.enterContext(patch('main.show_fatal_error'))

    def test_modes_use_unique_temporary_storage_and_clean_up(self):
        paths = []

        def start(*args, **kwargs):
            path = Path(kwargs['storage_path'])
            paths.append(path)
            self.assertTrue(path.is_dir())
            self.assertIs(kwargs['private_mode'], self.config['private_mode'])
            (path / 'session-data').write_text('temporary', encoding='utf-8')

        def wait():
            self.assertTrue(paths[-1].exists())

        self.start.side_effect = start
        self.wait.side_effect = wait
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.config['private_mode'] = enabled
                self.assertEqual(main.main(), 0)
                self.assertFalse(paths[-1].exists())
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(self.wait.call_count, 2)
        self.fatal.assert_not_called()

    def test_start_failure_still_cleans_up_storage_service_and_tray(self):
        paths = []

        def fail(*args, **kwargs):
            paths.append(Path(kwargs['storage_path']))
            raise RuntimeError('startup failed')

        self.start.side_effect = fail
        with self.assertLogs(level='ERROR'):
            self.assertEqual(main.main(), 1)
        self.assertFalse(paths[0].exists())
        self.wait.assert_called_once()
        self.controller.stop.assert_called_once()
        self.tray.stop.assert_called_once()

    def test_invalid_mode_can_still_open_configuration(self):
        self.config['private_mode'] = 'invalid'
        self.assertEqual(main.main(), 0)
        self.assertIs(self.start.call_args.kwargs['private_mode'], False)
        self.fatal.assert_not_called()

    def test_initial_wait_page_uses_configured_window_title(self):
        self.config.update(web_command='python -m http.server', web_url='http://127.0.0.1:3080/',
                           window_title='自定义工作台')
        with patch('main.create_main_window') as create_window:
            self.assertEqual(main.main(), 0)
        source = create_window.call_args.args[2]
        self.assertIn('<span class="brand-name" title="自定义工作台">自定义工作台</span>', source)
        self.assertIn('正在启动服务', source)
        self.fatal.assert_not_called()


class SingleInstanceTests(unittest.TestCase):
    """命名互斥体单实例保护（P0）。"""

    def test_first_instance_acquires_second_is_rejected(self):
        # 同一进程内第二次创建同名互斥体 → ERROR_ALREADY_EXISTS → 返回 False
        self.assertTrue(main.acquire_single_instance())
        self.assertFalse(main.acquire_single_instance())


if __name__ == '__main__':
    unittest.main()
