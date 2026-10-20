"""多项目启动器界面：项目卡片列表 + 服务状态 + 实时日志抽屉（单页 HTML）。

与 :mod:`app.pages` 的关系：pages.py 承载单服务模式的等待页 / 错误页 / 配置页；
本模块只承载多项目模式的主界面，单独成文件以免 pages.py 继续膨胀。

实现约定：
- 页面模板不使用 `string.Template`：模板内含大量 JS，`$` 与 `` ` `` 会与占位符冲突，
  因此改用显式标记（`__WBD_TITLE__` / `__WBD_BOOTSTRAP__`）+ `str.replace` 注入；
- 结构化数据通过一次 bootstrap JSON 注入，避免在 HTML 里拼字符串；
- 前端只能调用白名单接口（项目 ID + 操作名），不能传命令，全部命令来自 Python 侧配置。
"""

import html
import json
import os
import sys

# 随程序分发的第三方前端资源目录（xterm.js 等）。打包后由 PyInstaller 解压到 _MEIPASS 下。
WEB_ASSET_DIR = "web"
# 资源缺失时为空字符串：页面会自动退回内置的轻量日志面板（不依赖任何外部资源）
_XTERM_FILES = ("xterm.css", "xterm.js", "xterm-fit.js")


def get_web_asset_dir() -> str:
    """
    返回第三方前端资源目录的绝对路径。

    - 打包运行（PyInstaller）：`<_MEIPASS>/app/web`；
    - 源码运行：`<项目>/app/web`。

    Returns:
        资源目录路径（不保证存在）。
    """
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled:
        return os.path.join(bundled, "app", WEB_ASSET_DIR)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), WEB_ASSET_DIR)


def read_web_asset(name: str) -> str:
    """
    读取第三方前端资源的文本内容。

    Args:
        name: 文件名（如 xterm.js）。

    Returns:
        文件内容；文件不存在或读取失败时返回空字符串（调用方据此降级）。
    """
    path = os.path.join(get_web_asset_dir(), name)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""

# 页面模板：占位符用 __WBD_*__ 标记，见模块 docstring
LAUNCHER_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__WBD_TITLE__</title>
<style>
  :root {
    color-scheme: light;
    --bg: #f4f6f5; --card: #ffffff; --ink: #1f2b26; --muted: #6a7a72;
    --line: #e2e8e4; --accent: #2f7d5f; --accent-ink: #ffffff;
    --ok: #2f9e6b; --warn: #d69327; --err: #cf4b3f; --idle: #9aa8a1; --info: #3b7dd8;
    --log-bg: #10161a; --log-ink: #d7e2dc;
  }
  html[data-theme="dark"] {
    color-scheme: dark;
    --bg: #12181a; --card: #1a2226; --ink: #e6edea; --muted: #93a49c;
    --line: #2a363a; --accent: #3f9c76; --accent-ink: #08120e;
    --ok: #43b581; --warn: #e0a63d; --err: #e2685c; --idle: #6b7b74; --info: #5b9bea;
    --log-bg: #0c1114; --log-ink: #cfdad4;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font: 14px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif;
    color: var(--ink); background: var(--bg); min-height: 100vh;
    display: flex; flex-direction: column;
  }
  button {
    font: inherit; color: var(--ink); background: transparent;
    border: 1px solid var(--line); border-radius: 8px;
    padding: 6px 12px; cursor: pointer; transition: .15s;
  }
  button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  button.primary:hover:not(:disabled) { filter: brightness(1.08); color: var(--accent-ink); }
  button.danger:hover:not(:disabled) { border-color: var(--err); color: var(--err); }
  header {
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    padding: 14px 22px; background: var(--card); border-bottom: 1px solid var(--line);
  }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 650; font-size: 16px; }
  .brand .logo { width: 30px; height: 30px; border-radius: 9px; background: var(--accent); color: var(--accent-ink); display: grid; place-items: center; }
  .spacer { flex: 1; }
  .hint { color: var(--muted); font-size: 12px; }
  main { flex: 1; padding: 20px 22px 28px; overflow: auto; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .card-head { display: flex; align-items: flex-start; gap: 12px; }
  .icon { width: 42px; height: 42px; border-radius: 11px; object-fit: cover; background: var(--bg); display: grid; place-items: center; font-weight: 700; color: var(--accent); flex-shrink: 0; }
  .titles { min-width: 0; flex: 1; }
  .titles h2 { font-size: 16px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .titles .url { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); white-space: nowrap; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--idle); flex-shrink: 0; }
  .s-running .dot, .dot.running { background: var(--ok); }
  .s-starting .dot, .dot.starting { background: var(--warn); animation: pulse 1.1s ease-in-out infinite; }
  .s-stopping .dot, .dot.stopping { background: var(--warn); }
  .s-error .dot, .dot.error { background: var(--err); }
  .s-partial .dot, .dot.partial { background: var(--info); }
  @keyframes pulse { 50% { opacity: .25; } }
  .services { border-top: 1px solid var(--line); padding-top: 10px; display: flex; flex-direction: column; gap: 7px; }
  .svc { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .svc .name { font-weight: 600; min-width: 74px; }
  .svc .meta { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .svc .detail { color: var(--err); font-size: 12px; }
  /* 启动序号 / 依赖标记 / 启动顺序链：把 dependsOn 的拓扑关系可视化 */
  .svc .order { width: 18px; height: 18px; border-radius: 50%; background: var(--bg); color: var(--muted); font-size: 11px; display: grid; place-items: center; flex-shrink: 0; }
  .svc .dep { color: var(--info); border: 1px solid color-mix(in srgb, var(--info) 35%, transparent); border-radius: 999px; padding: 0 7px; font-size: 11.5px; white-space: nowrap; flex-shrink: 0; }
  .chain { color: var(--muted); font-size: 12px; padding-top: 2px; }
  .failure { color: var(--err); font-size: 12.5px; background: color-mix(in srgb, var(--err) 10%, transparent); border-radius: 8px; padding: 8px 10px; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; border-top: 1px solid var(--line); padding-top: 12px; }
  .empty { text-align: center; color: var(--muted); padding: 60px 20px; }
  .issues { margin-bottom: 16px; border: 1px solid var(--err); border-radius: 12px; padding: 12px 16px; background: color-mix(in srgb, var(--err) 8%, transparent); }
  .issues h3 { font-size: 13.5px; font-weight: 650; margin-bottom: 6px; color: var(--err); }
  .issues ul { margin: 0; padding-left: 20px; font-size: 12.5px; }
  /* 日志抽屉 */
  .drawer {
    position: fixed; left: 0; right: 0; bottom: 0; height: 42vh; min-height: 200px;
    background: var(--log-bg); color: var(--log-ink); border-top: 1px solid var(--line);
    display: none; flex-direction: column; z-index: 20;
  }
  .drawer.open { display: flex; }
  .drawer-head { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid #2a363a; flex-wrap: wrap; }
  .drawer-head .title { font-weight: 600; margin-right: 4px; }
  .drawer-head button { color: var(--log-ink); border-color: #33454d; padding: 4px 10px; font-size: 12.5px; }
  .drawer-head button.active { border-color: var(--accent); color: var(--accent); }
  .tabs { display: flex; gap: 8px; flex-wrap: wrap; }
  .log-view {
    flex: 1; overflow: auto; padding: 8px 12px 14px; margin: 0;
    font: 12.5px/1.55 "Cascadia Mono", Consolas, "Courier New", monospace;
    white-space: pre-wrap; word-break: break-all;
  }
  .ln-err { color: #ff8b7e; }
  .ln-warn { color: #f0c274; }
  .ln-info { color: #7fd1ac; }
  .ln-sys { color: #7f97a3; font-style: italic; }
  .toast {
    position: fixed; right: 18px; bottom: 18px; background: var(--card); color: var(--ink);
    border: 1px solid var(--line); border-left: 3px solid var(--accent);
    border-radius: 10px; padding: 10px 14px; font-size: 13px; box-shadow: 0 8px 24px rgba(0,0,0,.14);
    display: none; max-width: 380px; z-index: 40;
  }
  /* xterm.js 终端容器（资源可用时启用；不可用时用上面的 .log-view） */
  .term-host { flex: 1; min-height: 0; padding: 6px 8px; display: none; }
  .term-host.active { display: block; }
  .drawer.terminal-mode .log-view { display: none; }
</style>
__WBD_XTERM_CSS__
</head>
<body>
<header>
  <div class="brand"><span class="logo">▶</span><span id="app-title">__WBD_TITLE__</span></div>
  <span class="hint" id="summary"></span>
  <span class="spacer"></span>
  <button id="btn-reload" title="重新读取 config.json 中的项目配置">重新加载配置</button>
  <button id="btn-config" title="用系统默认编辑器打开配置文件">打开配置</button>
  <button id="btn-theme">深色</button>
  <button id="btn-exit" class="danger">退出程序</button>
</header>
<main>
  <div class="issues" id="issues" style="display:none">
    <h3>项目配置存在以下问题（修正 config.json 后点「重新加载配置」）</h3>
    <ul id="issues-list"></ul>
  </div>
  <div class="grid" id="cards"></div>
  <div class="empty" id="empty" style="display:none">还没有配置任何项目：请点击右上角「打开配置」编辑 config.json 的 projects 数组。</div>
</main>

<div class="drawer" id="drawer">
  <div class="drawer-head">
    <span class="title" id="drawer-title">实时日志</span>
    <div class="tabs" id="log-tabs"></div>
    <span class="spacer"></span>
    <button id="btn-autoscroll" class="active">自动滚动</button>
    <button id="btn-clearlog">清空</button>
    <button id="btn-closedrawer">收起</button>
  </div>
  <pre class="log-view" id="log-view"></pre>
  <div class="term-host" id="term-host"></div>
</div>
<div class="toast" id="toast"></div>

__WBD_XTERM_JS__
<script>
"use strict";
// bootstrap：Python 侧注入的初始状态
var BOOTSTRAP = __WBD_BOOTSTRAP__;

var state = BOOTSTRAP.state || { projects: [] };
var offsets = {};        // 日志键 -> 前端已持有的行数偏移
var logLines = {};       // 日志键 -> 已渲染的行数组（仅当前打开的键）
var activeKey = "";      // 当前日志面板显示的服务
var autoScroll = true;
var resyncing = {};

function $(id) { return document.getElementById(id); }

function toast(message) {
  var el = $("toast");
  el.textContent = message;
  el.style.display = "block";
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(function () { el.style.display = "none"; }, 3200);
}

function api() {
  return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
}

function call(name, args, okMessage) {
  var bridge = api();
  if (!bridge || typeof bridge[name] !== "function") { toast("界面尚未就绪，请稍候"); return; }
  Promise.resolve(bridge[name].apply(bridge, args || [])).then(function (result) {
    if (result && result.ok === false) { toast(result.message || "操作失败"); }
    else if (okMessage) { toast(result && result.message ? result.message : okMessage); }
    refresh();
  }).catch(function (err) { toast("调用失败：" + err); });
}

var STATUS_TEXT = {
  running: "运行中", starting: "启动中", stopping: "停止中",
  stopped: "已停止", partial: "部分运行", error: "异常"
};

function statusClass(status) { return "s-" + (status || "stopped"); }

function serviceMeta(svc) {
  var parts = [];
  if (svc.port) { parts.push("端口 " + svc.port); }
  if (svc.pid) { parts.push("PID " + svc.pid); }
  parts.push("就绪：" + (svc.readyWhen || "process"));
  return parts.join(" · ");
}

function monogramIcon(project) {
  var mono = document.createElement("div");
  mono.className = "icon";
  mono.textContent = (project.name || "?").slice(0, 1).toUpperCase();
  return mono;
}

// 图标元素：有 icon 时用 <img>，加载失败或未配置时回退到「项目名首字母」占位。
// 本地图标由 Python 侧读成 data URL 传入（页面无法直接引用本地文件路径）。
function renderIcon(project) {
  if (!project.icon) { return monogramIcon(project); }
  var img = document.createElement("img");
  img.className = "icon";
  img.alt = "";
  img.addEventListener("error", function () {
    if (img.parentNode) { img.parentNode.replaceChild(monogramIcon(project), img); }
  });
  img.src = project.icon;
  return img;
}

function renderCards() {
  var host = $("cards");
  var projects = state.projects || [];
  $("empty").style.display = projects.length ? "none" : "block";
  host.innerHTML = "";
  var running = 0;
  projects.forEach(function (project) {
    if (project.status === "running") { running += 1; }
    var card = document.createElement("div");
    card.className = "card " + statusClass(project.status);

    var head = document.createElement("div");
    head.className = "card-head";
    head.appendChild(renderIcon(project));
    var titles = document.createElement("div");
    titles.className = "titles";
    titles.innerHTML =
      '<h2>' + escapeHtml(project.name) + "</h2>" +
      '<div class="url">' + escapeHtml(project.url || "（未配置打开地址）") + "</div>";
    head.appendChild(titles);
    var badge = document.createElement("span");
    badge.className = "badge";
    badge.innerHTML = '<span class="dot"></span>' + (STATUS_TEXT[project.status] || project.status);
    head.appendChild(badge);
    card.appendChild(head);

    var list = document.createElement("div");
    list.className = "services";
    (project.services || []).forEach(function (svc) {
      var row = document.createElement("div");
      row.className = "svc";
      // 启动序号（拓扑排序结果）与依赖标记，让「谁先起、依赖谁」一眼可见
      var orderBadge = svc.order
        ? '<span class="order" title="启动顺序第 ' + svc.order + ' 位">' + svc.order + "</span>"
        : "";
      var depChip = (svc.dependsOn && svc.dependsOn.length)
        ? '<span class="dep" title="必须先就绪的服务">依赖 ' + escapeHtml(svc.dependsOn.join("、")) + "</span>"
        : "";
      row.innerHTML =
        orderBadge +
        '<span class="dot ' + (svc.status || "stopped") + '"></span>' +
        '<span class="name">' + escapeHtml(svc.name) + "</span>" +
        depChip +
        '<span class="meta" title="' + escapeAttr(svc.command || "") + '">' + escapeHtml(serviceMeta(svc)) + "</span>" +
        (svc.status === "error" && svc.detail ? '<span class="detail" title="' + escapeAttr(svc.detail) + '">' + escapeHtml(svc.detail) + "</span>" : "");
      list.appendChild(row);
    });
    var startOrder = project.startOrder || [];
    if (startOrder.length > 1) {
      var chain = document.createElement("div");
      chain.className = "chain";
      chain.textContent = "启动顺序：" + startOrder.map(function (name, index) {
        return (index + 1) + " " + name;
      }).join("  →  ");
      list.appendChild(chain);
    }
    card.appendChild(list);

    if (project.failure) {
      var failure = document.createElement("div");
      failure.className = "failure";
      failure.textContent = project.failure;
      card.appendChild(failure);
    }

    var actions = document.createElement("div");
    actions.className = "actions";
    var busy = !!project.busy;
    var busyTask = project.busyTask || "";
    // 启动/重启进行中：停止按钮变成「取消启动」并保持可用（长超时的服务不必干等）
    var starting = busyTask === "start" || busyTask === "restart";
    actions.appendChild(button("启动", "primary", busy || project.status === "running", function () {
      call("start_project", [project.id], "已开始启动");
    }));
    actions.appendChild(button(starting ? "取消启动" : "停止", starting ? "danger" : "",
      starting ? false : (busy || project.status === "stopped"), function () {
        call("stop_project", [project.id], starting ? "已请求取消启动" : "已开始停止");
      }));
    actions.appendChild(button("重启", "", busy || project.status === "stopped", function () {
      call("restart_project", [project.id], "已开始重启");
    }));
    actions.appendChild(button("打开页面", "", !project.url || project.status !== "running", function () {
      call("open_project_page", [project.id]);
    }));
    actions.appendChild(button("日志", "", false, function () {
      openDrawer(project);
    }));
    card.appendChild(actions);
    host.appendChild(card);
  });
  $("summary").textContent = projects.length
    ? ("共 " + projects.length + " 个项目，" + running + " 个运行中")
    : "";
}

function button(label, className, disabled, onClick) {
  var el = document.createElement("button");
  el.textContent = label;
  if (className) { el.className = className; }
  el.disabled = !!disabled;
  if (!disabled) { el.addEventListener("click", onClick); }
  return el;
}

function escapeHtml(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function escapeAttr(text) { return escapeHtml(text); }

// ---------------- 日志 ----------------
function logKey(projectId, serviceName) { return projectId + "/" + serviceName; }

function openDrawer(project) {
  var services = project.services || [];
  if (!services.length) { return; }
  var tabs = $("log-tabs");
  tabs.innerHTML = "";
  services.forEach(function (svc) {
    var key = logKey(project.id, svc.name);
    var tab = document.createElement("button");
    tab.textContent = svc.name;
    tab.addEventListener("click", function () {
      selectLog(project, key);
      Array.prototype.forEach.call(tabs.children, function (child) { child.classList.remove("active"); });
      tab.classList.add("active");
    });
    tabs.appendChild(tab);
  });
  // 打开抽屉时固定切到该项目的第一个服务，行为可预期
  tabs.children[0].classList.add("active");
  $("drawer").classList.add("open");
  ensureTerminal();
  selectLog(project, logKey(project.id, services[0].name));
}

function selectLog(project, key) {
  activeKey = key;
  $("drawer-title").textContent = project.name + " · 实时日志";
  if (!logLines[key]) { logLines[key] = []; }
  renderLog();
  pullLogs(key, offsets[key] || 0);
}

// xterm.js 终端：资源可用时启用（原生支持 ANSI 颜色、可选中复制、滚动缓冲）；
// 资源缺失或初始化失败时保持为空，页面自动退回下面的 <pre> 渲染。
var term = null;
var termFit = null;

function terminalAvailable() {
  return typeof window.Terminal === "function";
}

function ensureTerminal() {
  if (term || !terminalAvailable()) { return; }
  var host = $("term-host");
  if (!host) { return; }
  try {
    term = new window.Terminal({
      convertEol: true,
      disableStdin: true,
      scrollback: 5000,
      fontSize: 12.5,
      fontFamily: '"Cascadia Mono", Consolas, "Courier New", monospace',
      theme: { background: "#0c1114", foreground: "#cfdad4", cursor: "#3f9c76", selectionBackground: "#2a363a" }
    });
    if (window.FitAddon && typeof window.FitAddon.FitAddon === "function") {
      termFit = new window.FitAddon.FitAddon();
      term.loadAddon(termFit);
    }
    term.open(host);
    host.classList.add("active");
    $("drawer").classList.add("terminal-mode");
    fitTerminal();
    window.setTimeout(fitTerminal, 60);
    window.addEventListener("resize", fitTerminal);
  } catch (e) {
    // 初始化失败就整体退回轻量面板 —— 日志功能绝不能因此不可用
    term = null;
    termFit = null;
    if (host) { host.classList.remove("active"); }
    $("drawer").classList.remove("terminal-mode");
  }
}

function fitTerminal() {
  if (!termFit) { return; }
  try { termFit.fit(); } catch (e) {}
}

function writeToTerminal(lines) {
  if (!term) { return; }
  lines.forEach(function (line) { term.write(line + "\r\n"); });
  if (autoScroll) { term.scrollToBottom(); }
}

function renderLog() {
  if (term) {
    // 整屏重建：切换服务、日志重同步、清空时走这里
    term.reset();
    writeToTerminal(logLines[activeKey] || []);
    return;
  }
  var view = $("log-view");
  var lines = logLines[activeKey] || [];
  view.innerHTML = lines.map(function (line) {
    return '<span class="' + lineClass(line) + '">' + escapeHtml(line) + "</span>";
  }).join("\n");
  if (autoScroll) { view.scrollTop = view.scrollHeight; }
}

function lineClass(line) {
  var text = line.toUpperCase();
  if (line.indexOf("[WebDesktop]") === 0) { return "ln-sys"; }
  if (text.indexOf("ERROR") >= 0 || text.indexOf("TRACEBACK") >= 0 || text.indexOf("FAILED") >= 0) { return "ln-err"; }
  if (text.indexOf("WARN") >= 0) { return "ln-warn"; }
  if (text.indexOf("INFO") >= 0) { return "ln-info"; }
  return "";
}

function appendLines(key, lines) {
  if (!logLines[key]) { logLines[key] = []; }
  // 只保留最近 3000 行，避免前端内存持续增长
  logLines[key] = logLines[key].concat(lines).slice(-3000);
  if (key !== activeKey) { return; }
  if (term) { writeToTerminal(lines); } else { renderLog(); }
}

function pullLogs(key, offset) {
  if (resyncing[key]) { return; }
  var bridge = api();
  if (!bridge || typeof bridge.get_logs !== "function") { return; }
  resyncing[key] = true;
  Promise.resolve(bridge.get_logs(key, offset)).then(function (result) {
    resyncing[key] = false;
    if (!result) { return; }
    if (result.dropped) {
      logLines[key] = [];
      appendLines(key, ["===== 日志过快，前 " + result.dropped + " 行已被丢弃 ====="].concat(result.lines || []));
    } else {
      appendLines(key, result.lines || []);
    }
    offsets[key] = result.offset || 0;
  }).catch(function () { resyncing[key] = false; });
}

// Python → 前端推送入口
window.__wbd = {
  state: function (payload) {
    if (payload && payload.projects) { state = payload; renderCards(); }
  },
  logs: function (payload) {
    Object.keys(payload || {}).forEach(function (key) {
      var item = payload[key] || {};
      var lines = item.lines || [];
      var offset = item.offset || 0;
      var current = offsets[key] || 0;
      if (offset - lines.length === current) {
        appendLines(key, lines);
        offsets[key] = offset;
      } else if (!resyncing[key]) {
        // 偏移不连续（前端漏收推送）：重新拉取，保证日志不乱序
        pullLogs(key, current);
      }
    });
  }
};

function refresh() {
  var bridge = api();
  if (!bridge || typeof bridge.get_state !== "function") { return; }
  Promise.resolve(bridge.get_state()).then(function (payload) {
    if (payload && payload.projects) { state = payload; renderCards(); }
  }).catch(function () {});
}

// ---------------- 事件绑定 ----------------
$("btn-reload").addEventListener("click", function () { call("reload_config", [], "配置已重新加载"); });
$("btn-config").addEventListener("click", function () { call("open_config_file", []); });
$("btn-exit").addEventListener("click", function () { call("exit_app", []); });
$("btn-theme").addEventListener("click", function () {
  var dark = document.documentElement.getAttribute("data-theme") === "dark";
  document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
  $("btn-theme").textContent = dark ? "深色" : "浅色";
  try { localStorage.setItem("wbd-theme", dark ? "light" : "dark"); } catch (e) {}
});
$("btn-autoscroll").addEventListener("click", function () {
  autoScroll = !autoScroll;
  $("btn-autoscroll").classList.toggle("active", autoScroll);
  if (autoScroll) {
    if (term) { term.scrollToBottom(); } else { renderLog(); }
  }
});
$("btn-clearlog").addEventListener("click", function () {
  if (activeKey && api()) { call("clear_logs", [activeKey]); }
  logLines[activeKey] = [];
  offsets[activeKey] = offsets[activeKey] || 0;
  renderLog();
});
$("btn-closedrawer").addEventListener("click", function () { $("drawer").classList.remove("open"); });
window.addEventListener("keydown", function (event) {
  if (event.key === "Escape") { $("drawer").classList.remove("open"); }
});

(function init() {
  try {
    var saved = localStorage.getItem("wbd-theme");
    if (saved === "dark" || saved === "light") {
      document.documentElement.setAttribute("data-theme", saved);
      $("btn-theme").textContent = saved === "dark" ? "浅色" : "深色";
    }
  } catch (e) {}
  renderCards();
  // 兜底轮询：推送通道异常时状态仍能刷新
  window.setInterval(refresh, 2000);
  window.addEventListener("pywebviewready", refresh);
})();
</script>
</body>
</html>
"""


# 注入到目标项目页面上的「返回启动器」悬浮按钮脚本：
# 在主窗口内打开项目页面时使用（不另开窗口，避免运行期多窗口带来的稳定性风险）。
# 幂等：重复注入只保留一个按钮；点击后经 js_api 切回启动器页面。
BACK_BUTTON_SCRIPT = (
    "(function () {"
    "  'use strict';"
    "  if (document.getElementById('__wbd_back_button')) { return; }"
    "  var button = document.createElement('button');"
    "  button.id = '__wbd_back_button';"
    "  button.type = 'button';"
    "  button.textContent = '← 返回启动器';"
    "  button.title = '返回 WebDesktop 多项目启动器';"
    "  button.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;"
    "padding:8px 14px;border-radius:999px;border:1px solid rgba(0,0,0,.12);"
    "background:rgba(255,255,255,.95);color:#24332d;"
    "font:13px/1.4 \"Segoe UI\",\"Microsoft YaHei\",sans-serif;cursor:pointer;"
    "box-shadow:0 2px 10px rgba(0,0,0,.2);';"
    "  button.addEventListener('click', function () {"
    "    if (window.pywebview && window.pywebview.api && window.pywebview.api.back_to_launcher) {"
    "      window.pywebview.api.back_to_launcher();"
    "    }"
    "  });"
    "  function attach() {"
    "    if (document.body) { document.body.appendChild(button); }"
    "    else { window.setTimeout(attach, 100); }"
    "  }"
    "  attach();"
    "})();"
)


def build_launcher_page(state: dict = None, window_title: str = None) -> str:
    """
    生成多项目启动器主页面 HTML。

    Args:
        state: 初始状态快照（`ProjectManager.state()` 的结果），可为空。
        window_title: 窗口标题 / 页面标题。

    Returns:
        完整 HTML 字符串。
    """
    bootstrap = json.dumps(
        {"state": state or {"projects": []}}, ensure_ascii=False
    ).replace("<", "\\u003c")
    title = html.escape((window_title or "").strip() or "WebDesktop 多项目启动器")
    # 第三方资源（xterm.js）内联注入：页面是 html= 注入式加载的，不能引用外部文件路径。
    # 资源缺失时注入空串，页面会自动退回内置的轻量日志面板。
    xterm_css = read_web_asset("xterm.css")
    xterm_js = read_web_asset("xterm.js")
    xterm_fit = read_web_asset("xterm-fit.js")
    vendor_css = f"<style>\n{xterm_css}\n</style>" if xterm_css else ""
    vendor_js = ""
    if xterm_js:
        vendor_js = f"<script>\n{xterm_js}\n</script>"
        if xterm_fit:
            vendor_js += f"\n<script>\n{xterm_fit}\n</script>"
    return (
        LAUNCHER_PAGE_TEMPLATE
        .replace("__WBD_TITLE__", title)
        .replace("__WBD_BOOTSTRAP__", bootstrap)
        .replace("__WBD_XTERM_CSS__", vendor_css)
        .replace("__WBD_XTERM_JS__", vendor_js)
    )
