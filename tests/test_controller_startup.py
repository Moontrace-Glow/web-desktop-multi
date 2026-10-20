from html import escape
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.controller import AppController
from app.pages import build_config_page, build_error_page, build_wait_page
from app.service import WebServiceError


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.window_title = '我的工作台 <开发> & 测试'
        self.controller = AppController({
            'web_url': 'http://127.0.0.1:3080', 'window_title': self.window_title,
        })
        self.loaded = threading.Event()
        self.window = Mock(events=SimpleNamespace(loaded=self.loaded))
        self.controller._window = self.window
        self.service_class = self.enterContext(patch('app.controller.WebServiceManager'))
        self.service = self.service_class.return_value
        self.wait_loop = self.enterContext(patch.object(self.controller, '_wait_loop'))

    def test_service_waits_for_initial_page_before_starting(self):
        attempted_wait = threading.Event()
        original_wait = self.loaded.wait

        def wait(timeout=None):
            attempted_wait.set()
            return original_wait(timeout)

        self.loaded.wait = wait
        worker = threading.Thread(target=self.controller.start)
        worker.start()
        try:
            self.assertTrue(attempted_wait.wait(1))
            self.service.start.assert_not_called()
            self.window.load_html.assert_not_called()
            self.loaded.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.service.start.assert_called_once()
            self.window.load_html.assert_called_once()
        finally:
            self.controller.stop_event.set()
            self.loaded.set()
            worker.join(1)

    def test_close_while_waiting_does_not_start_service(self):
        def close_during_wait(timeout):
            self.controller.stop_event.set()
            return False

        self.loaded.wait = Mock(side_effect=close_during_wait)
        self.controller.start()
        self.loaded.wait.assert_called()
        self.service_class.assert_not_called()
        self.window.load_html.assert_not_called()

    def test_config_mode_does_not_wait_or_start_service(self):
        self.controller._config_mode = True
        self.loaded.wait = Mock()
        self.controller.start()
        self.loaded.wait.assert_not_called()
        self.service_class.assert_not_called()

    def test_immediate_failure_is_shown_after_initial_page(self):
        self.loaded.set()
        self.service.start.side_effect = WebServiceError('missing executable')
        self.controller.start()
        html = self.window.load_html.call_args.args[0]
        self.assertIn('服务启动失败', html)
        self.assertIn('missing executable', html)
        self.wait_loop.assert_not_called()

    def test_retry_stops_previous_service_and_restarts(self):
        self.loaded.set()
        previous_service = Mock()
        self.controller._service = previous_service
        self.controller.retry()
        previous_service.stop.assert_called_once()
        self.service.start.assert_called_once()
        self.assertIs(self.controller._service, self.service)
        self.assertIn(escape(self.window_title), self.window.load_html.call_args.args[0])

    def test_return_from_config_preserves_window_title(self):
        for has_error in (False, True):
            with self.subTest(has_error=has_error):
                self.controller._last_error_html = None
                if has_error:
                    self.controller._show_error('服务启动失败', '启动命令不可用')
                self.window.reset_mock()
                self.controller._config_page_source = 'error'
                self.controller.exit_config_page()
                self.window.load_html.assert_called_once()
                source = self.window.load_html.call_args.args[0]
                self.assertIn(escape(self.window_title), source)
                self.assertIn('服务启动失败' if has_error else '正在启动服务', source)

    def test_missing_title_falls_back_during_start_and_error(self):
        self.controller._config.pop('window_title')
        self.loaded.set()
        self.service.read_log_tail.return_value = ''
        self.controller.start()
        self.assertIn('title="WebDesktop">WebDesktop</span>', self.window.load_html.call_args.args[0])
        self.controller._show_error('服务已停止', '服务进程退出')
        self.assertIn('title="WebDesktop">WebDesktop</span>', self.window.load_html.call_args.args[0])

    def test_wait_page_transitions_to_ready_service(self):
        for source in ('fixed', 'log'):
            with self.subTest(source=source):
                self.window.reset_mock()
                self.controller._config['url_source'] = source
                self.service.is_running.return_value = True
                self.service.is_ready.side_effect = [False, True]
                self.service.find_log_url.return_value = 'http://127.0.0.1:3080/session'
                with patch('app.controller.time.sleep') as sleep:
                    AppController._wait_loop(self.controller, self.service)
                sleep.assert_called_once()
                expected = (self.service.find_log_url.return_value if source == 'log'
                            else self.controller._config['web_url'])
                self.window.load_url.assert_called_once_with(expected)
                self.window.load_html.assert_not_called()
                self.assertTrue(self.controller._target_loaded)

    def test_stopped_or_timed_out_service_shows_error_with_logs(self):
        for running, title in ((False, '服务已停止'), (True, '服务启动超时')):
            with self.subTest(running=running):
                self.window.reset_mock()
                self.controller._service = self.service
                self.service.is_running.return_value = running
                self.service.is_ready.return_value = False
                self.service.exit_code.return_value = 1
                self.service.read_log_tail.return_value = 'Error: <service> failed & stopped'
                with patch('app.controller.time.monotonic', side_effect=[0, 61]):
                    AppController._wait_loop(self.controller, self.service)
                self.window.load_html.assert_called_once()
                source = self.window.load_html.call_args.args[0]
                self.assertIn(title, source)
                self.assertIn(escape(self.service.read_log_tail.return_value), source)
                self.assertEqual(self.controller._last_error_html, source)
                self.assertFalse(self.controller._target_loaded)
                self.window.load_url.assert_not_called()


class WaitPageTests(unittest.TestCase):
    def test_wait_page_shows_status_and_initial_timer(self):
        source = build_wait_page('http://127.0.0.1:3080/', '我的工作台')
        self.assertIn('<h1>正在启动服务</h1>', source)
        self.assertIn('服务就绪后将自动打开页面', source)
        self.assertIn('<span id="sec">0</span>', source)
        self.assertIn('role="timer" aria-live="off"', source)
        self.assertIn('关闭窗口将按配置最小化到系统托盘或退出程序', source)

    def test_target_url_is_escaped_as_text(self):
        url = 'http://127.0.0.1:3080/?q=</code><script>alert("x")</script>&label=中文\' $value'
        source = build_wait_page(url, '我的工作台')
        self.assertIn(escape(url), source)
        self.assertNotIn(url, source)
        self.assertEqual(source.count('<script>'), 1)

    def test_wait_page_supports_small_windows_and_reduced_motion(self):
        source = build_wait_page('http://127.0.0.1:3080/', '我的工作台')
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', source)
        self.assertIn('overflow-wrap: anywhere', source)
        self.assertIn('@media (prefers-reduced-motion: reduce)', source)
        self.assertIn('.spinner { animation: none; }', source)


class ErrorPageTests(unittest.TestCase):
    def test_error_page_shows_message_and_log_limit(self):
        source = build_error_page('服务已停止', '进程退出（退出码：1）', 'first\n  second', 15, window_title='我的工作台')
        self.assertIn('<h1 id="error-title">服务已停止</h1>', source)
        self.assertIn('进程退出（退出码：1）', source)
        self.assertIn('末尾 15 行', source)
        self.assertIn('first\n  second', source)
        self.assertIn('.log-box { display: block;', source)
        self.assertIn('tabindex="0" role="region" aria-label="服务日志内容"', source)

    def test_error_page_hides_empty_logs(self):
        source = build_error_page('服务启动失败', '找不到启动命令', window_title='我的工作台')
        self.assertIn('.log-box { display: none;', source)
        self.assertIn('找不到启动命令', source)
        self.assertEqual(source.count('<button '), 3)

    def test_error_title_message_and_logs_are_escaped(self):
        payload = '</pre><script>alert("x")</script><img src=x onerror="alert(1)"> & 中文\' $value'
        source = build_error_page(payload, payload, payload, window_title='我的工作台')
        self.assertEqual(source.count(escape(payload)), 4)
        self.assertNotIn('<script>', source)
        self.assertNotIn('<img ', source)

    def test_error_page_preserves_native_actions(self):
        source = build_error_page('服务启动超时', '等待 60 秒后服务仍未就绪', window_title='我的工作台')
        for handler in ('pywebview.api.retry()', "pywebview.api.open_config_page('error')",
                        'pywebview.api.exit_app()'):
            with self.subTest(handler=handler):
                self.assertIn(f'onclick="{handler}"', source)
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', source)
        self.assertIn('.btn:focus-visible', source)


class PageWindowTitleTests(unittest.TestCase):
    def test_window_title_is_escaped_in_text_and_tooltip(self):
        for title in ('我的工作台', '\"><img src=x onerror="alert(1)"> & 中文\' $value'):
            for page, source in (
                ('wait', build_wait_page('http://127.0.0.1:3080/', title)),
                ('error', build_error_page('服务已停止', '服务进程退出', window_title=title)),
                ('config', build_config_page({'window_title': title}, config_path='preview/config.json')),
            ):
                with self.subTest(page=page, title=title):
                    escaped = escape(title)
                    self.assertIn(f'<span class="brand-name" title="{escaped}">{escaped}</span>', source)
                    self.assertNotIn('<img ', source)
                    self.assertNotIn('>WebDesktop</span>', source)

    def test_empty_or_missing_title_uses_webdesktop_on_every_page(self):
        for config in ({}, {'window_title': None}, {'window_title': ''}, {'window_title': ' \t\n '}):
            for page, source in (
                ('wait', build_wait_page('http://127.0.0.1:3080/', **config)),
                ('error', build_error_page('服务已停止', '服务进程退出', **config)),
                ('config', build_config_page(config, config_path='preview/config.json')),
            ):
                with self.subTest(page=page, config=config):
                    self.assertIn('<span class="brand-name" title="WebDesktop">WebDesktop</span>', source)


if __name__ == '__main__':
    unittest.main()
