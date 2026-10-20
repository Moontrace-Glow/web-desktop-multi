"""可拖动齿轮按钮注入脚本：跳转到目标网页后由控制器通过 evaluate_js 注入页面。

脚本在页面右下角（默认位置）叠加一个圆形齿轮图标，鼠标按住即可拖动到任意位置，
单击直接打开启动器配置页遮罩（无悬浮菜单、无气泡提示：图标本身就是入口）。
位置由 Python 侧状态文件持久化（`~/.WebDesktop/ui_state.json`）：拖动结束经 js 桥
`save_toolbar_pos` 写回，注入时把已保存位置一并注入，因此页面刷新与应用重启后都能
恢复到用户拖到的位置（页面 localStorage 仅作为 js 桥不可用时的兜底）。

关键设计：
- 宿主节点挂载在 documentElement（框架重渲染不会清理），按钮渲染在 Shadow DOM 中，
  样式 `:host { all: initial }` 完全隔离，不受目标网页样式影响。
- 仅使用 pointerdown / pointermove / pointerup + setPointerCapture，
  并通过位移阈值区分「单击」与「拖动」，避免拖动后误触发打开配置页。
- 默认位置用 CSS `right / bottom` 表达，不依赖 DOM 测量即可正确落位；
  拖动后按可移动区域的横纵比例定位，窗口缩放不覆盖原始比例。
- 脚本幂等：window.__wbdToolbarInjected 标记防止整页刷新、导航后重复注入。
"""

import json

# 位置持久化键：按页面 origin 保存在浏览器本站存储中（WebView2 用户数据目录）
STORAGE_KEY = "wbd.toolbar.pos"

# 图标默认位置距窗口右侧 / 底部的像素距离（右下角）
DEFAULT_MARGIN = 12

# 图标距视口边缘的最小间距（限位用）
EDGE_GAP = 4

# 判定为「拖动」的最小位移（像素）：小于该值视为单击
DRAG_THRESHOLD = 4

TOOLBAR_SCRIPT = """
(function () {
  'use strict';
  // 幂等保护：整页刷新 / 导航后由 loaded 事件再次注入时直接跳过
  if (window.__wbdToolbarInjected) { return; }
  window.__wbdToolbarInjected = true;

  var STORAGE_KEY = '%(storage_key)s';
  var DEFAULT_MARGIN = %(default_margin)d;
  var EDGE_GAP = %(edge_gap)d;
  var DRAG_THRESHOLD = %(drag_threshold)d;

  // 宿主节点 + Shadow DOM：样式与目标网页完全隔离
  var host = document.createElement('div');
  host.setAttribute('data-wbd-toolbar', '1');
  var shadow = host.attachShadow({ mode: 'open' });
  shadow.innerHTML = '' +
    '<style>' +
    '  :host { all: initial; }' +
    '  .wrap { position: fixed; z-index: 2147483621; right: ' + DEFAULT_MARGIN + 'px;' +
    '          bottom: ' + DEFAULT_MARGIN + 'px; display: flex; align-items: flex-end;' +
    '          user-select: none; -webkit-user-select: none; touch-action: none; }' +
    '  .btn { box-sizing: border-box; width: 34px; height: 34px; padding: 0; margin: 0;' +
    '         display: flex; align-items: center; justify-content: center;' +
    '         border-radius: 50%%; background: rgba(255, 255, 255, 0.94); color: #334155;' +
    '         border: 1px solid rgba(15, 23, 42, 0.16);' +
    '         box-shadow: 0 4px 14px rgba(15, 23, 42, 0.22);' +
    '         cursor: grab; touch-action: none; -webkit-user-select: none; user-select: none; }' +
    '  .btn:hover { background: #ffffff; color: #0f172a; }' +
    '  .wrap.dragging .btn { cursor: grabbing; }' +
    '</style>' +
    '<div class="wrap">' +
    '  <div class="btn" role="button" tabindex="0" aria-label="打开启动器配置页（可拖动）"' +
    '       title="打开启动器配置页（按住可拖动）">' +
    '    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
    '         stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"' +
    '         aria-hidden="true" focusable="false">' +
    '      <circle cx="12" cy="12" r="3.2"></circle>' +
    '      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06' +
    'a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09' +
    'A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06' +
    'a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09' +
    'A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06' +
    'a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09' +
    'a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06' +
    'a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09' +
    'a1.65 1.65 0 0 0-1.51 1z"></path>' +
    '    </svg>' +
    '  </div>' +
    '</div>';
  (document.documentElement || document.body).appendChild(host);

  var wrap = shadow.querySelector('.wrap');
  var button = shadow.querySelector('.btn');
  var positionRatio = null;

  // 由 Python 侧注入的已保存位置（注入时定值，不依赖页面存储）
  var INITIAL_POS = %(initial_pos)s;

  // 位置持久化：优先经 js 桥写入 Python 侧状态文件（~/.WebDesktop/ui_state.json），
  // 桥不可用（如在普通浏览器中打开页面）时退化为页面 localStorage，
  // localStorage 在 pywebview 私有模式下不跨应用重启保留，仅作为兜底。
  function savePos(left, top) {
    var payload = {
      left: left,
      top: top,
      vw: window.innerWidth,
      vh: window.innerHeight
    };
    var api = window.pywebview && window.pywebview.api;
    if (api && api.save_toolbar_pos) {
      try {
        api.save_toolbar_pos(payload.left, payload.top, payload.vw, payload.vh);
        return;
      } catch (e) {
        // 桥调用异常时继续走 localStorage 兜底
      }
    }
    if (api) {
      // 桥对象已就绪但方法尚未挂上（极端时序）：稍后重试，避免这次拖动的位置丢失
      queueResave(payload);
      return;
    }
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (e) {
      // 本地存储不可用：仅影响下次注入时的位置还原，不影响本次拖动
    }
  }

  // 桥未就绪时暂存位置并在稍后重试（最多约 6 秒），成功一次即停止
  function queueResave(payload) {
    var attempts = 0;
    var timer = setInterval(function () {
      attempts += 1;
      var api = window.pywebview && window.pywebview.api;
      if (api && api.save_toolbar_pos) {
        clearInterval(timer);
        try {
          api.save_toolbar_pos(payload.left, payload.top, payload.vw, payload.vh);
        } catch (e) {
          // 仍失败则由后续拖动再次触发保存
        }
        return;
      }
      if (attempts >= 30) { clearInterval(timer); }
    }, 200);
  }

  function movementRange(vw, vh) {
    return {
      width: Math.max(0, vw - (wrap.offsetWidth || 34) - 2 * EDGE_GAP),
      height: Math.max(0, vh - (wrap.offsetHeight || 34) - 2 * EDGE_GAP)
    };
  }

  // 将坐标限制在视口内，保证图标始终可见（至少保留可点击区域）
  function clamp(left, top) {
    var range = movementRange(window.innerWidth, window.innerHeight);
    return {
      left: Math.max(EDGE_GAP, Math.min(left, EDGE_GAP + range.width)),
      top: Math.max(EDGE_GAP, Math.min(top, EDGE_GAP + range.height))
    };
  }

  // 用 left / top 定位（覆盖 CSS 的 right / bottom 默认值）
  function place(left, top) {
    wrap.style.right = 'auto';
    wrap.style.bottom = 'auto';
    wrap.style.left = left + 'px';
    wrap.style.top = top + 'px';
  }

  function setPositionRatio(left, top, vw, vh) {
    var range = movementRange(vw, vh);
    positionRatio = {
      x: Math.max(0, Math.min(1, (left - EDGE_GAP) / (range.width || 1))),
      y: Math.max(0, Math.min(1, (top - EDGE_GAP) / (range.height || 1)))
    };
  }

  function applyPositionRatio() {
    if (!positionRatio) { return; }
    var range = movementRange(window.innerWidth, window.innerHeight);
    // 缩放只换算显示坐标，不反推比例，避免往返缩放积累误差。
    place(EDGE_GAP + positionRatio.x * range.width, EDGE_GAP + positionRatio.y * range.height);
  }

  // 注入时还原位置：Python 侧状态优先，否则使用页面 localStorage。
  function restorePos() {
    var pos = INITIAL_POS;
    if (!pos) {
      try {
        var raw = window.localStorage.getItem(STORAGE_KEY);
        if (raw) { pos = JSON.parse(raw); }
      } catch (e) {
        // 本地存储不可用或内容损坏：使用默认右下角
        pos = null;
      }
    }
    if (!pos || typeof pos.left !== 'number' || typeof pos.top !== 'number') { return; }
    var vw = pos.vw || window.innerWidth;
    var vh = pos.vh || window.innerHeight;
    setPositionRatio(pos.left, pos.top, vw, vh);
    applyPositionRatio();
  }

  // 当前图标位置：优先取 left，未拖动过（left 为 auto）时回退到 CSS 右下角实测值
  function currentPoint() {
    var style = window.getComputedStyle(wrap);
    var raw = style.left;
    var left = raw && raw !== 'auto' ? parseFloat(raw) : NaN;
    var top = parseFloat(style.top);
    if (isNaN(left) || isNaN(top)) {
      var rect = wrap.getBoundingClientRect();
      left = rect.left;
      top = rect.top;
    }
    return { left: left, top: top };
  }

  // 打开启动器配置页（遮罩，目标网页状态保留）
  function openConfig() {
    var api = window.pywebview && window.pywebview.api;
    if (api && api.open_config_page) {
      api.open_config_page('web');
      return;
    }
    // 桥不可用（如页面在普通浏览器中打开）时仅提示，不影响页面
    console.warn('[WebDesktop] pywebview 桥不可用，无法打开启动器配置页');
  }

  // 拖动状态：pointerdown 记录指针落点与图标位置，pointermove 按位移更新
  var dragging = false;
  var pressing = false;
  var suppressClick = false;
  var downX = 0;
  var downY = 0;
  var offsetX = 0;
  var offsetY = 0;

  function onPointerDown(event) {
    if (event.button !== 0) { return; }
    var point = currentPoint();
    downX = event.clientX;
    downY = event.clientY;
    // 记录指针在图标内的落点，移动时保持相对位置，避免图标跳动
    offsetX = event.clientX - point.left;
    offsetY = event.clientY - point.top;
    dragging = false;
    pressing = true;
    wrap.classList.add('dragging');
    try { button.setPointerCapture(event.pointerId); } catch (e) { /* 忽略：部分内核不支持捕获 */ }
  }

  function onPointerMove(event) {
    if (!pressing) { return; }
    if (!dragging) {
      // 位移超过阈值后才视为拖动，避免手抖导致误判
      if (Math.abs(event.clientX - downX) + Math.abs(event.clientY - downY) < DRAG_THRESHOLD) {
        return;
      }
      dragging = true;
    }
    // 拖动期间阻止默认行为，避免选中目标网页文字
    event.preventDefault();
    var point = clamp(event.clientX - offsetX, event.clientY - offsetY);
    setPositionRatio(point.left, point.top, window.innerWidth, window.innerHeight);
    place(point.left, point.top);
  }

  function onPointerUp(event) {
    if (!pressing) { return; }
    pressing = false;
    wrap.classList.remove('dragging');
    try { button.releasePointerCapture(event.pointerId); } catch (e) { /* 忽略 */ }
    if (!dragging) { return; }
    dragging = false;
    // 标记「刚结束一次拖动」：本次 pointerup 之后浏览器仍会派发 click，必须拦掉
    suppressClick = true;
    var point = clamp(event.clientX - offsetX, event.clientY - offsetY);
    setPositionRatio(point.left, point.top, window.innerWidth, window.innerHeight);
    place(point.left, point.top);
    savePos(point.left, point.top);
    // 拖动结束清除可能残留的页面文字选区
    var selection = window.getSelection();
    if (selection && selection.removeAllRanges) { selection.removeAllRanges(); }
  }

  function onPointerCancel() {
    pressing = false;
    dragging = false;
    wrap.classList.remove('dragging');
  }

  button.addEventListener('pointerdown', onPointerDown);
  button.addEventListener('pointermove', onPointerMove);
  button.addEventListener('pointerup', onPointerUp);
  button.addEventListener('pointercancel', onPointerCancel);
  button.addEventListener('click', function (event) {
    event.preventDefault();
    // 刚结束拖动：本次 click 是拖动的副产物，只拦截不打开配置页
    if (suppressClick || dragging) {
      suppressClick = false;
      return;
    }
    openConfig();
  });
  button.addEventListener('keydown', function (event) {
    // 键盘可达性：Enter / 空格等同于单击
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openConfig();
    }
  });

  window.addEventListener('resize', applyPositionRatio);

  restorePos();
})();
"""


def build_toolbar_script(pos=None) -> str:
    """
    生成「可拖动齿轮按钮」注入脚本。

    脚本在目标网页右下角叠加一个圆形齿轮图标：单击打开启动器配置页遮罩，
    按住可拖动到任意位置。位置由 Python 侧状态文件持久化
    （拖动结束经 js 桥 save_toolbar_pos 写回 ~/.WebDesktop/ui_state.json），
    注入时把已保存位置一并注入，因此同一应用会话内页面刷新也会保持位置。

    Args:
        pos: 已保存的位置（app.ui_state.load_toolbar_pos 的返回值）；None 表示
            使用默认右下角。由保存的坐标与视口尺寸还原可移动区域内的横纵比例。

    Returns:
        可交由 window.evaluate_js 执行的注入脚本字符串。
    """
    # 与配置页一致：把 < 转义为 \u003c，避免内容破坏 <script>/HTML 解析
    if isinstance(pos, dict):
        initial_pos = json.dumps(pos, ensure_ascii=False).replace("<", "\\u003c")
    else:
        initial_pos = "null"
    return TOOLBAR_SCRIPT % {
        "storage_key": STORAGE_KEY,
        "default_margin": DEFAULT_MARGIN,
        "edge_gap": EDGE_GAP,
        "drag_threshold": DRAG_THRESHOLD,
        "initial_pos": initial_pos,
    }
