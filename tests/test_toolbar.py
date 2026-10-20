import json
import shutil
import subprocess
import unittest

from app.toolbar import build_toolbar_script


DOM_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const handlers = {};
const windowHandlers = {};
const saved = [];
const opened = [];
let stored = input.stored === undefined ? null : JSON.stringify(input.stored);
const style = {};
const wrap = {
  style, offsetWidth: 34, offsetHeight: 34,
  classList: { add() {}, remove() {} },
  getBoundingClientRect() {
    return {
      left: style.left ? parseFloat(style.left) : win.innerWidth - 46,
      top: style.top ? parseFloat(style.top) : win.innerHeight - 46
    };
  }
};
const button = {
  addEventListener(type, handler) { handlers[type] = handler; },
  setPointerCapture() {}, releasePointerCapture() {}
};
const shadow = { querySelector: selector => selector === '.wrap' ? wrap : button };
const host = { setAttribute() {}, attachShadow: () => shadow };
const win = {
  innerWidth: input.width, innerHeight: input.height,
  addEventListener(type, handler) { windowHandlers[type] = handler; },
  getComputedStyle: () => ({ left: style.left || 'auto', top: style.top || 'auto' }),
  getSelection: () => null,
  localStorage: {
    getItem: () => stored,
    setItem(key, value) { stored = value; }
  }
};
if (input.bridge !== false) {
  win.pywebview = { api: {
    save_toolbar_pos(...args) { saved.push(args); },
    open_config_page(source) { opened.push(source); }
  } };
}
vm.runInNewContext(input.script, {
  window: win,
  document: { createElement: () => host, documentElement: { appendChild() {} } },
  console
});
function snapshot() {
  return { ...wrap.getBoundingClientRect(), saved: saved.slice(), opened: opened.slice(), stored };
}
function pointer(type, x, y) {
  handlers[type]({ button: 0, pointerId: 1, clientX: x, clientY: y, preventDefault() {} });
}
const results = [snapshot()];
for (const action of input.actions) {
  if (action.type === 'resize') {
    win.innerWidth = action.width;
    win.innerHeight = action.height;
    windowHandlers.resize();
  } else if (action.type === 'drag') {
    const point = wrap.getBoundingClientRect();
    pointer('pointerdown', point.left + 17, point.top + 17);
    pointer('pointermove', action.left + 17, action.top + 17);
    pointer('pointerup', action.left + 17, action.top + 17);
    handlers.click({ preventDefault() {} });
  } else if (action.type === 'click') {
    handlers.click({ preventDefault() {} });
  } else if (action.type === 'key') {
    handlers.keydown({ key: action.key, preventDefault() {} });
  }
  results.push(snapshot());
}
process.stdout.write(JSON.stringify(results));
"""


def resize(width, height):
    return {'type': 'resize', 'width': width, 'height': height}


def ratio_point(x, y, width=1200, height=800):
    return 4 + x * max(0, width - 42), 4 + y * max(0, height - 42)


@unittest.skipUnless(shutil.which('node'), 'Node.js is required to execute the toolbar script')
class ToolbarTests(unittest.TestCase):
    def run_toolbar(self, pos=None, actions=(), width=1200, height=800, **options):
        result = subprocess.run(
            ['node', '-e', DOM_HARNESS],
            input=json.dumps({
                'script': build_toolbar_script(pos),
                'width': width, 'height': height, 'actions': actions, **options,
            }),
            text=True, encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_points(self, results, points):
        self.assertEqual(len(results), len(points))
        for result, (left, top) in zip(results, points):
            self.assertAlmostEqual(result['left'], left)
            self.assertAlmostEqual(result['top'], top)

    def test_default_stays_in_bottom_right_through_repeated_resizes(self):
        sizes = [(1920, 1080), (800, 600), (1200, 800)] * 3
        results = self.run_toolbar(actions=[resize(w, h) for w, h in sizes])
        self.assert_points(results, [(1154, 754)] + [(w - 46, h - 46) for w, h in sizes])
        self.assertTrue(all(not result['saved'] for result in results))

    def test_saved_positions_keep_percentages_through_repeated_resizes(self):
        sizes = [(1920, 1080), (800, 600), (987, 654), (1200, 800)] * 10
        for x, y in [(0, 0), (1, 0), (0, 1), (1, 1), (0.5, 0.5), (0.8, 0.7)]:
            with self.subTest(x=x, y=y):
                left, top = ratio_point(x, y)
                results = self.run_toolbar(
                    {'left': left, 'top': top, 'vw': 1200, 'vh': 800},
                    [resize(w, h) for w, h in sizes],
                )
                self.assert_points(results, [(left, top)] + [ratio_point(x, y, w, h) for w, h in sizes])
                self.assertTrue(all(not result['saved'] for result in results))

    def test_saved_maximized_position_restores_percentage_in_smaller_window(self):
        left, top = ratio_point(0.8, 0.7, 1920, 1080)
        results = self.run_toolbar(
            {'left': left, 'top': top, 'vw': 1920, 'vh': 1080},
            [resize(1920, 1080), resize(1200, 800)],
        )
        self.assert_points(results, [ratio_point(0.8, 0.7), (left, top), ratio_point(0.8, 0.7)])

    def test_zero_movement_range_does_not_replace_percentage(self):
        left, top = ratio_point(0.8, 0.7)
        results = self.run_toolbar(
            {'left': left, 'top': top, 'vw': 1200, 'vh': 800},
            [resize(42, 42), resize(0, 0), resize(30, 30), resize(1200, 800)],
        )
        self.assert_points(results, [(left, top), (4, 4), (4, 4), (4, 4), (left, top)])
        self.assertEqual(results[-1]['saved'], [])

    def test_missing_viewport_uses_initial_window_for_percentage(self):
        left, top = ratio_point(0.8, 0.7)
        for viewport in [{}, {'vw': 0, 'vh': 0}]:
            with self.subTest(viewport=viewport):
                results = self.run_toolbar(
                    {'left': left, 'top': top, **viewport},
                    [resize(1920, 1080), resize(1200, 800)],
                )
                self.assert_points(results, [(left, top), ratio_point(0.8, 0.7, 1920, 1080), (left, top)])

    def test_drag_updates_percentage_and_saves_only_user_position(self):
        left, top = ratio_point(0.8, 0.7)
        sizes = [(1920, 1080), (800, 600), (1200, 800)]
        results = self.run_toolbar(actions=[
            {'type': 'drag', 'left': left, 'top': top},
            *[resize(w, h) for w, h in sizes],
        ])
        self.assert_points(results, [(1154, 754), (left, top)] + [ratio_point(0.8, 0.7, w, h) for w, h in sizes])
        self.assertEqual(results[-1]['saved'], [[left, top, 1200, 800]])
        self.assertEqual(results[-1]['opened'], [])

    def test_drag_after_resize_can_change_percentage(self):
        left, top = ratio_point(0.8, 0.7)
        new_left, new_top = ratio_point(0.25, 0.35, 1920, 1080)
        results = self.run_toolbar(
            {'left': left, 'top': top, 'vw': 1200, 'vh': 800},
            [resize(1920, 1080), {'type': 'drag', 'left': new_left, 'top': new_top}, resize(1200, 800)],
        )
        self.assert_points(results, [
            (left, top), ratio_point(0.8, 0.7, 1920, 1080), (new_left, new_top), ratio_point(0.25, 0.35),
        ])
        self.assertEqual(results[-1]['saved'], [[new_left, new_top, 1920, 1080]])

    def test_local_storage_restores_same_percentage_after_reload(self):
        left, top = ratio_point(0.8, 0.7)
        results = self.run_toolbar(
            actions=[{'type': 'drag', 'left': left, 'top': top}], bridge=False,
        )
        stored = json.loads(results[-1]['stored'])
        self.assertEqual(stored, {'left': left, 'top': top, 'vw': 1200, 'vh': 800})
        restored = self.run_toolbar(
            width=1920, height=1080, stored=stored, bridge=False,
            actions=[resize(1200, 800)],
        )
        self.assert_points(restored, [ratio_point(0.8, 0.7, 1920, 1080), (left, top)])

    def test_bridge_saved_position_restores_same_percentage(self):
        left, top = ratio_point(0.8, 0.7)
        results = self.run_toolbar(actions=[
            {'type': 'drag', 'left': left, 'top': top}, resize(1920, 1080),
        ])
        saved = dict(zip(('left', 'top', 'vw', 'vh'), results[-1]['saved'][0]))
        restored = self.run_toolbar(saved, width=1920, height=1080)
        self.assert_points(restored, [ratio_point(0.8, 0.7, 1920, 1080)])

    def test_out_of_bounds_drag_stays_at_percentage_limits(self):
        results = self.run_toolbar(actions=[
            {'type': 'drag', 'left': -100, 'top': 9999}, resize(1920, 1080), resize(1200, 800),
        ])
        self.assert_points(results, [(1154, 754), (4, 762), (4, 1042), (4, 762)])
        self.assertEqual(results[-1]['saved'], [[4, 762, 1200, 800]])

    def test_out_of_bounds_saved_position_is_limited_to_zero_and_one(self):
        results = self.run_toolbar(
            {'left': -100, 'top': 9999, 'vw': 1200, 'vh': 800}, [resize(1920, 1080)],
        )
        self.assert_points(results, [(4, 762), (4, 1042)])

    def test_invalid_stored_position_keeps_default_position(self):
        results = self.run_toolbar(
            stored={'left': 'bad', 'top': 50}, actions=[resize(800, 600), resize(1200, 800)],
        )
        self.assert_points(results, [(1154, 754), (754, 554), (1154, 754)])

    def test_click_and_keyboard_still_open_config_after_drag(self):
        results = self.run_toolbar(actions=[
            {'type': 'drag', 'left': 50, 'top': 70},
            {'type': 'click'}, {'type': 'key', 'key': 'Enter'}, {'type': 'key', 'key': ' '},
        ])
        self.assertEqual(results[1]['opened'], [])
        self.assertEqual(results[-1]['opened'], ['web', 'web', 'web'])


if __name__ == '__main__':
    unittest.main()
