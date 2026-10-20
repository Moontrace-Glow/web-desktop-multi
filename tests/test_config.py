import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from app.config import DEFAULT_CONFIG, build_config_from_form, get_config_issues, load_config
from app.controller import AppController
from app.pages import build_config_page


class PrivateModeConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch('app.config.get_config_dir', return_value=self.directory))
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.config.update(web_command='python -m http.server', web_url='http://127.0.0.1:8080')

    def test_new_config_defaults_to_normal_mode(self):
        self.assertIs(load_config()['private_mode'], False)
        stored = json.loads((Path(self.directory) / 'config.json').read_text(encoding='utf-8'))
        self.assertIs(stored['private_mode'], False)

    def test_existing_config_without_switch_uses_normal_mode(self):
        self.config.pop('private_mode')
        self.write(self.config)
        self.assertIs(load_config()['private_mode'], False)

    def test_missing_window_title_defaults_to_webdesktop(self):
        self.config.pop('window_title')
        self.write(self.config)
        self.assertEqual(load_config()['window_title'], 'WebDesktop')

    def test_explicit_modes_survive_loading_and_form_save(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.config['private_mode'] = enabled
                self.write(self.config)
                loaded = load_config()
                self.assertIs(loaded['private_mode'], enabled)
                self.assertEqual(get_config_issues(loaded), [])
                controller = AppController(loaded)
                self.assertTrue(controller.save_config(loaded)['ok'])
                self.assertIs(load_config()['private_mode'], enabled)

    def test_invalid_file_value_is_rejected(self):
        for value in ('false', 0, [], {}):
            with self.subTest(value=value):
                self.config['private_mode'] = value
                self.assertTrue(any('private_mode' in issue for issue in get_config_issues(self.config)))

    def test_form_parses_boolean_strings_and_rejects_invalid_value(self):
        for value, expected in (('true', True), ('false', False)):
            with self.subTest(value=value):
                self.config['private_mode'] = value
                config, error = build_config_from_form(self.config)
                self.assertEqual(error, '')
                self.assertIs(config['private_mode'], expected)
        self.config['private_mode'] = 'invalid'
        config, error = build_config_from_form(self.config)
        self.assertEqual(config, {})
        self.assertIn('private_mode', error)

    def write(self, config):
        (Path(self.directory) / 'config.json').write_text(json.dumps(config), encoding='utf-8')


class _ConfigPageParser(HTMLParser):
    """Collect structural attributes and script data without executing the page."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.scripts = []
        self.text = []
        self._in_script = False
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag == 'script':
            self.scripts.append('')
            self._in_script = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.scripts[-1] += data
        else:
            self.text.append(data)


class ConfigPageTests(unittest.TestCase):
    def setUp(self):
        # Rendering must not resolve a real user's configuration path.
        self.config_path = 'C:/config-page-tests/config.json'
        self.path_mock = self.enterContext(patch(
            'app.pages.get_config_path', return_value=self.config_path,
        ))
        self.source = build_config_page(DEFAULT_CONFIG)
        self.page = _ConfigPageParser(self.source)

    def element_by_id(self, element_id, page=None):
        page = self.page if page is None else page
        matches = [
            element for element in page.elements
            if element[1].get('id') == element_id
        ]
        self.assertEqual(len(matches), 1, f'Expected one element with id={element_id!r}')
        return matches[0]

    @staticmethod
    def initially_hidden(attrs):
        # Inspect initial markup only; this is not a CSS or JavaScript engine.
        return 'hidden' in attrs or bool(re.search(
            r'(?:^|;)\s*display\s*:\s*none\s*(?:!\s*important\s*)?(?:;|$)',
            attrs.get('style', ''), re.IGNORECASE,
        ))

    def embedded_config(self, page):
        assignments = [
            script[match.end():].lstrip()
            for script in page.scripts
            for match in re.finditer(r'\b(?:var|let|const)\s+CONFIG\s*=\s*', script)
        ]
        self.assertEqual(len(assignments), 1, 'Expected one CONFIG JSON initializer')
        # Decode one JSON value rather than splitting at braces/semicolons in strings.
        config, end = json.JSONDecoder().raw_decode(assignments[0])
        self.assertTrue(assignments[0][end:].lstrip().startswith(';'))
        return config

    def test_default_page_renders_complete_html_and_config(self):
        self.assertIsInstance(self.source, str)
        for tag in ('html', 'head', 'body'):
            with self.subTest(tag=tag):
                self.assertEqual(sum(name == tag for name, _ in self.page.elements), 1)
        self.assertIn(self.config_path, ''.join(self.page.text))
        self.assertEqual(self.embedded_config(self.page), DEFAULT_CONFIG)

    def test_every_default_config_field_has_native_inputs(self):
        radio_options = {
            'url_source': {'fixed', 'log'},
            'close_action': {'minimize_to_tray', 'exit'},
        }
        # projects 是多项目模式的结构化数组，由用户直接编辑 config.json（启动器界面
        # 提供「打开配置」按钮），不在本配置页表单中呈现，故不参与本用例校验
        json_edited_fields = {'projects'}
        for key, value in DEFAULT_CONFIG.items():
            with self.subTest(field=key):
                if key in json_edited_fields:
                    continue
                if key in radio_options:
                    inputs = [
                        attrs for tag, attrs in self.page.elements
                        if tag == 'input' and attrs.get('name') == key
                    ]
                    self.assertEqual(len(inputs), len(radio_options[key]))
                    self.assertEqual({attrs.get('value') for attrs in inputs}, radio_options[key])
                    self.assertTrue(all(attrs.get('type') == 'radio' for attrs in inputs))
                    continue
                ids = ('window_width', 'window_height') if key == 'window_size' else (key,)
                if isinstance(value, bool):
                    types = {'checkbox'}
                elif isinstance(value, (int, float)) or key == 'window_size':
                    types = {'number'}
                else:
                    types = {'text', 'url'}
                for element_id in ids:
                    tag, attrs = self.element_by_id(element_id)
                    self.assertEqual(tag, 'input')
                    self.assertIn(attrs.get('type', 'text'), types)

    def test_dom_ids_are_unique(self):
        ids = [attrs['id'] for _, attrs in self.page.elements if 'id' in attrs]
        self.assertTrue(ids)
        self.assertTrue(all(ids), 'DOM ids must not be empty')
        self.assertEqual(len(ids), len(set(ids)), 'Duplicate DOM ids')

    def test_tabs_control_the_three_panels(self):
        tabs = [attrs for _, attrs in self.page.elements if attrs.get('role') == 'tab']
        panels = [attrs for _, attrs in self.page.elements if attrs.get('role') == 'tabpanel']
        expected = {'panel-service', 'panel-window', 'panel-advanced'}
        self.assertEqual(len(tabs), 3)
        self.assertEqual(len(panels), 3)
        self.assertEqual({panel.get('id') for panel in panels}, expected)
        self.assertEqual({tab.get('aria-controls') for tab in tabs}, expected)
        for tab in tabs:
            with self.subTest(tab=tab.get('id')):
                _, panel = self.element_by_id(tab['aria-controls'])
                self.assertEqual(panel.get('role'), 'tabpanel')
                self.assertEqual(panel.get('aria-labelledby'), tab.get('id'))

    def test_label_for_targets_exist_and_are_inputs(self):
        targets = [
            attrs['for'] for tag, attrs in self.page.elements
            if tag == 'label' and 'for' in attrs
        ]
        self.assertTrue(targets)
        for target in targets:
            with self.subTest(target=target):
                tag, _ = self.element_by_id(target)
                self.assertEqual(tag, 'input')

    def test_only_service_panel_is_initially_visible_and_selected(self):
        for name in ('service', 'window', 'advanced'):
            with self.subTest(panel=name):
                _, panel = self.element_by_id(f'panel-{name}')
                self.assertEqual(self.initially_hidden(panel), name != 'service')
                tabs = [
                    attrs for _, attrs in self.page.elements
                    if attrs.get('role') == 'tab'
                    and attrs.get('aria-controls') == panel['id']
                ]
                self.assertEqual(len(tabs), 1)
                self.assertEqual(tabs[0].get('aria-selected'), str(name == 'service').lower())

    def test_fixed_url_source_initially_hides_regex_field(self):
        self.assertEqual(self.embedded_config(self.page)['url_source'], 'fixed')
        _, field = self.element_by_id('regex-field')
        self.assertTrue(self.initially_hidden(field))
        tag, _ = self.element_by_id('url_log_regex')
        self.assertEqual(tag, 'input')

    def test_close_button_respects_show_close(self):
        _, default_close = self.element_by_id('btn-close')
        self.assertTrue(self.initially_hidden(default_close))
        for show_close in (True, False):
            with self.subTest(show_close=show_close):
                page = _ConfigPageParser(build_config_page(DEFAULT_CONFIG, show_close=show_close))
                tag, attrs = self.element_by_id('btn-close', page)
                self.assertEqual(tag, 'button')
                self.assertEqual(attrs.get('type'), 'button')
                self.assertEqual(self.initially_hidden(attrs), not show_close)

    def test_form_defers_validation_to_js_and_has_save_button(self):
        tag, form = self.element_by_id('config-form')
        self.assertEqual(tag, 'form')
        self.assertIn('novalidate', form, 'JS must reveal a hidden tab before focusing errors')
        tag, save = self.element_by_id('btn-save')
        self.assertEqual(tag, 'button')
        self.assertEqual(save.get('type'), 'submit')

    def test_script_breakout_is_escaped_and_config_round_trips(self):
        for closing_tag in ('</script>', '</ScRiPt>'):
            with self.subTest(closing_tag=closing_tag):
                payload = (closing_tag + '<img id="injected-config" src=x onerror="alert(1)">'
                           + '\' & \\ 中文\n}; <!--')
                config = copy.deepcopy(DEFAULT_CONFIG)
                for key, value in config.items():
                    if isinstance(value, str):
                        config[key] = f'{key}: {payload}'
                config.update(window_size=[960, 640], private_mode=True, check_interval=1.25)
                original = copy.deepcopy(config)
                source = build_config_page(config)
                page = _ConfigPageParser(source)
                self.assertNotIn(payload, source)
                expected_elements = [
                    (tag, dict(attrs, title=config['window_title']) if attrs.get('class') == 'brand-name' else attrs)
                    for tag, attrs in self.page.elements
                ]
                self.assertEqual(page.elements, expected_elements, 'Config injected HTML elements/attributes')
                self.assertEqual(len(page.scripts), len(self.page.scripts))
                self.assertEqual(self.embedded_config(page), original)
                self.assertEqual(config, original, 'Rendering must not mutate configuration')

    def test_html_in_config_path_is_displayed_as_literal_text(self):
        path = 'C:/<img id="injected-path" src=x onerror="alert(1)">/&quot;\' & 配置.json'
        for use_default_path in (False, True):
            with self.subTest(use_default_path=use_default_path):
                self.path_mock.return_value = path if use_default_path else self.config_path
                source = build_config_page(
                    DEFAULT_CONFIG, config_path='' if use_default_path else path,
                )
                page = _ConfigPageParser(source)
                self.assertNotIn(path, source)
                self.assertIn(path, ''.join(page.text), 'Path must be escaped exactly once')
                self.assertEqual(page.elements, self.page.elements, 'Path injected HTML elements/attributes')
                self.assertEqual(len(page.scripts), len(self.page.scripts))
                self.assertEqual(self.embedded_config(page), DEFAULT_CONFIG)


if __name__ == '__main__':
    unittest.main()
