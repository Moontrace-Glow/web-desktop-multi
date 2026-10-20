"""页面模板模块：内置等待页、错误页与应用配置页的 HTML。"""

import html
import json
from string import Template

from app.config import get_config_path

# 等待页：服务启动期间展示，带旋转动画与计时器
WAIT_PAGE_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>正在启动服务</title>
<style>
  :root { color-scheme: light; --accent: #32745e; --ink: #24332d; --muted: #68776f; --line: #e1e7e3; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font: 14px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif;
    color: var(--ink); background: #f7f9f7;
    min-height: 100vh; min-height: 100dvh;
    display: flex; flex-direction: column;
  }
  svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; flex-shrink: 0; }
  .page-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 22px 40px; background: #fff; border-bottom: 1px solid var(--line); }
  .brand { display: flex; align-items: center; gap: 10px; min-width: 0; font-weight: 650; font-size: 17px; letter-spacing: -.4px; }
  .brand-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .brand .brand-icon { flex-shrink: 0; }
  .brand-icon { width: 34px; height: 34px; background: var(--accent); color: white; border-radius: 10px; display: grid; place-items: center; }
  .header-label { color: var(--muted); font-size: 12px; flex-shrink: 0; }
  main { flex: 1; display: grid; place-items: center; padding: 48px 24px; }
  .wait-content { width: 100%; max-width: 520px; min-width: 0; }
  .intro { text-align: center; margin-bottom: 30px; }
  .loading-icon { position: relative; width: 72px; height: 72px; margin: 0 auto 24px; display: grid; place-items: center; color: var(--accent); }
  .loading-icon::before { content: ""; position: absolute; inset: 10px; background: #eaf2ec; border-radius: 50%; }
  .loading-icon svg { position: relative; width: 24px; height: 24px; }
  .spinner { position: absolute; inset: 0; border: 3px solid #e1eae3; border-top-color: var(--accent); border-radius: 50%; animation: spin 1.2s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  h1 { font-size: 24px; font-weight: 650; letter-spacing: -.4px; }
  .sub { margin-top: 8px; font-size: 13px; color: var(--muted); }
  .card { border: 1px solid var(--line); background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 4px #263b2d02; }
  .card-header { padding: 16px 22px; border-bottom: 1px solid #edf0ed; display: flex; align-items: center; gap: 10px; }
  .card-header svg { color: var(--accent); width: 18px; height: 18px; }
  h2 { font-size: 14px; font-weight: 600; }
  .status-badge { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; color: var(--accent); border: 1px solid #dce9df; background: #f6faf6; border-radius: 5px; font-size: 11px; white-space: nowrap; }
  .status-badge::before { content: ""; width: 5px; height: 5px; border-radius: 50%; background: var(--accent); }
  .card-body { padding: 22px; }
  .url-label { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .url { display: block; padding: 12px 14px; border: 1px solid var(--line); border-radius: 6px; background: #fcfdfc; color: var(--accent); font: 13px/1.8 Consolas, "Microsoft YaHei", monospace; overflow-wrap: anywhere; user-select: text; }
  .connection-meta { margin-top: 18px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px 16px; color: var(--muted); font-size: 12px; }
  .timer { display: flex; align-items: center; gap: 7px; }
  .timer svg { width: 15px; height: 15px; }
  #sec { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
  .hint { display: flex; align-items: flex-start; gap: 8px; margin-top: 18px; padding: 0 3px; color: var(--muted); font-size: 11px; line-height: 1.8; }
  .hint svg { width: 15px; height: 15px; margin-top: 2px; }
  .window-note { padding: 0 24px 24px; color: var(--muted); text-align: center; font-size: 11px; }
  @media (max-width: 560px) {
    .page-header { padding: 16px 20px; }
    .header-label { font-size: 11px; }
    main { padding: 36px 20px; }
    h1 { font-size: 22px; }
    .card-header { padding: 14px 18px; }
    .card-body { padding: 18px; }
  }
  @media (max-height: 700px) {
    .page-header { padding-top: 16px; padding-bottom: 16px; }
    main { padding-top: 24px; padding-bottom: 24px; }
    .loading-icon { margin-bottom: 18px; }
    .intro { margin-bottom: 24px; }
  }
  @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
</style>
</head>
<body>
  <header class="page-header">
    <div class="brand"><span class="brand-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4m-5-10 3 3-3 3m6 0h4"/></svg></span><span class="brand-name" title="$WINDOW_TITLE">$WINDOW_TITLE</span></div>
    <span class="header-label">工作空间 / 启动</span>
  </header>
  <main>
    <div class="wait-content">
      <div class="intro" role="status">
        <div class="loading-icon" aria-hidden="true"><div class="spinner"></div><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3m6 0h4"/></svg></div>
        <h1>正在启动服务</h1>
        <p class="sub">稍等片刻，服务就绪后将自动打开页面。</p>
      </div>
      <section class="card" aria-labelledby="connection-title">
        <div class="card-header"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12h5l3-8 4 16 3-8h5"/></svg><h2 id="connection-title">服务连接</h2><span class="status-badge">启动中</span></div>
        <div class="card-body">
          <p class="url-label" id="url-label">服务地址</p>
          <code class="url" aria-labelledby="url-label">$TARGET_URL</code>
          <div class="connection-meta">
            <div class="timer" role="timer" aria-live="off"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><span>已等待 <span id="sec">0</span> 秒</span></div>
            <span>正在检查服务是否就绪</span>
          </div>
        </div>
      </section>
      <p class="hint"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></svg><span>如果长时间无响应，请检查启动命令与服务地址配置。</span></p>
    </div>
  </main>
  <footer class="window-note">关闭窗口将按配置最小化到系统托盘或退出程序</footer>
  <script>
    var startAt = Date.now();
    setInterval(function () {
      var seconds = Math.floor((Date.now() - startAt) / 1000);
      document.getElementById('sec').textContent = seconds;
    }, 1000);
  </script>
</body>
</html>
""")

# 错误页：启动失败 / 服务退出 / 启动超时时展示，可重试或退出
ERROR_PAGE_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$ERROR_TITLE</title>
<style>
  :root { color-scheme: light; --accent: #32745e; --ink: #24332d; --muted: #68776f; --line: #e1e7e3; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font: 14px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif;
    color: var(--ink); background: #f7f9f7;
    min-height: 100vh; min-height: 100dvh;
    display: flex; flex-direction: column;
  }
  svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; flex-shrink: 0; }
  .page-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 22px 40px; background: #fff; border-bottom: 1px solid var(--line); }
  .brand { display: flex; align-items: center; gap: 10px; min-width: 0; font-weight: 650; font-size: 17px; letter-spacing: -.4px; }
  .brand-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .brand .brand-icon { flex-shrink: 0; }
  .brand-icon { width: 34px; height: 34px; background: var(--accent); color: white; border-radius: 10px; display: grid; place-items: center; }
  .header-label { color: var(--muted); font-size: 12px; flex-shrink: 0; }
  main { flex: 1; display: grid; place-items: center; padding: 32px 24px; }
  .error-content { width: 100%; max-width: 680px; min-width: 0; }
  .card { border: 1px solid var(--line); background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 4px #263b2d02; }
  .error-summary { display: flex; align-items: flex-start; gap: 16px; padding: 28px; }
  .error-icon { width: 48px; height: 48px; display: grid; place-items: center; flex-shrink: 0; border: 1px solid #f0d8d2; border-radius: 12px; background: #fff4f2; color: #b55c4e; }
  .error-icon svg { width: 24px; height: 24px; }
  .error-copy { min-width: 0; }
  h1 { font-size: 22px; font-weight: 650; letter-spacing: -.4px; overflow-wrap: anywhere; }
  .message { margin-top: 8px; font-size: 13px; color: var(--muted); line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
  .log-box { display: $LOG_DISPLAY; margin: 0 28px 28px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
  .log-header { padding: 12px 16px; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid var(--line); }
  .log-header svg { color: var(--accent); width: 16px; height: 16px; }
  h2 { font-size: 13px; font-weight: 600; }
  .log-limit { margin-left: auto; font-size: 11px; color: var(--muted); background: #f3f6f3; padding: 2px 7px; border-radius: 4px; white-space: nowrap; }
  .log-content { max-height: 220px; overflow: auto; padding: 14px 16px; background: #f7f9f7; color: #53645a; font: 12px/1.8 Consolas, "Microsoft YaHei", monospace; white-space: pre-wrap; overflow-wrap: anywhere; scrollbar-width: thin; scrollbar-color: #bccbc1 #f7f9f7; }
  .log-content:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 18px 28px; border-top: 1px solid var(--line); }
  .btn { display: inline-flex; justify-content: center; align-items: center; gap: 7px; padding: 9px 16px; border: 1px solid transparent; border-radius: 6px; font: 12px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif; white-space: nowrap; cursor: pointer; transition: border-color .15s, background .15s, box-shadow .15s; }
  .btn svg { width: 15px; height: 15px; }
  .btn:focus-visible { outline: 3px solid #8db9a5; outline-offset: 3px; }
  .btn-primary { background: var(--accent); color: white; font-weight: 600; box-shadow: 0 2px 3px #32745e16; }
  .btn-primary:hover { background: #285e4d; }
  .btn-ghost { background: white; border-color: var(--line); color: #64736a; }
  .btn-ghost:hover { background: #f3f6f3; }
  .btn-exit { margin-left: auto; }
  .hint { display: flex; align-items: flex-start; gap: 8px; margin-top: 18px; padding: 0 3px; color: var(--muted); font-size: 11px; line-height: 1.8; }
  .hint svg { width: 15px; height: 15px; margin-top: 2px; }
  @media (max-width: 560px) {
    .page-header { padding: 16px 20px; }
    .header-label { font-size: 11px; }
    main { padding: 24px 20px; }
    .error-summary { padding: 20px; gap: 12px; }
    .error-icon { width: 40px; height: 40px; border-radius: 10px; }
    h1 { font-size: 20px; }
    .log-box { margin: 0 20px 20px; }
    .log-header, .log-content { padding: 12px; }
    .actions { padding: 16px 20px; gap: 8px; }
    .btn { padding: 9px 12px; }
  }
  @media (max-width: 380px) {
    .actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .btn-exit { grid-column: 1 / -1; margin-left: 0; }
  }
  @media (max-height: 700px) {
    .page-header { padding-top: 16px; padding-bottom: 16px; }
    main { padding-top: 24px; padding-bottom: 24px; }
    .error-summary { padding-top: 22px; padding-bottom: 22px; }
    .log-content { max-height: 160px; }
  }
  @media (prefers-reduced-motion: reduce) { .btn { transition: none; } }
</style>
</head>
<body>
  <header class="page-header">
    <div class="brand"><span class="brand-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4m-5-10 3 3-3 3m6 0h4"/></svg></span><span class="brand-name" title="$WINDOW_TITLE">$WINDOW_TITLE</span></div>
    <span class="header-label">工作空间 / 服务异常</span>
  </header>
  <main>
    <div class="error-content">
      <section class="card" aria-labelledby="error-title">
        <div class="error-summary" role="alert">
          <div class="error-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 3v1"/></svg></div>
          <div class="error-copy"><h1 id="error-title">$ERROR_TITLE</h1><p class="message">$ERROR_MESSAGE</p></div>
        </div>
        <section class="log-box" aria-labelledby="log-title">
          <div class="log-header"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8m-8 4h6"/></svg><h2 id="log-title">服务日志</h2><span class="log-limit">末尾 $LOG_LINES 行</span></div>
          <pre class="log-content" tabindex="0" role="region" aria-label="服务日志内容">$LOG_TAIL</pre>
        </section>
        <footer class="actions">
          <button type="button" class="btn btn-primary" onclick="pywebview.api.retry()"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/></svg>重试</button>
          <button type="button" class="btn btn-ghost" onclick="pywebview.api.open_config_page('error')"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h7m4 0h5M4 17h3m4 0h9"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></svg>打开配置</button>
          <button type="button" class="btn btn-ghost btn-exit" onclick="pywebview.api.exit_app()"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H4v16h5m5-12 4 4-4 4m-6-4h14"/></svg>退出</button>
        </footer>
      </section>
      <p class="hint"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></svg><span>可以重试启动服务，或打开配置检查启动命令与服务地址。</span></p>
    </div>
  </main>
</body>
</html>
""")

CONFIG_PAGE_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>应用配置</title>
<style>
  :root { color-scheme: light; --accent: #32745e; --ink: #24332d; --muted: #68776f; --line: #e1e7e3; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  [hidden] { display: none !important; }
  body { font: 14px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif; color: var(--ink); background: #f7f9f7; }
  button, input { font: inherit; }
  button { cursor: pointer; }
  button, input, .choice { transition: border-color .15s, background .15s, box-shadow .15s; }
  button:focus-visible, input:focus-visible { outline: 3px solid #8db9a5; outline-offset: 3px; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; flex-shrink: 0; }
  .app-shell { display: grid; grid-template-columns: 216px minmax(0, 1fr); height: 100vh; height: 100dvh; }
  .sidebar { padding: 32px 20px 22px; display: flex; flex-direction: column; border-right: 1px solid var(--line); background: #f0f4f0; min-height: 0; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 0 8px; min-width: 0; font-weight: 650; font-size: 17px; letter-spacing: -.4px; }
  .brand-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .brand-icon { width: 34px; height: 34px; background: var(--accent); color: white; border-radius: 10px; display: grid; place-items: center; flex-shrink: 0; }
  .nav-label { margin: 42px 12px 12px; color: #78857d; font-size: 11px; letter-spacing: 2px; }
  .nav-list { display: grid; gap: 6px; }
  .nav-item { border: 1px solid transparent; color: #66766c; background: transparent; border-radius: 8px; padding: 12px; display: flex; align-items: center; gap: 11px; text-align: left; }
  .nav-item:hover { background: #e8eee9; }
  .nav-item[aria-selected="true"] { background: #fff; color: var(--accent); border-color: var(--line); box-shadow: 0 2px 3px #23372a05; font-weight: 600; }
  .nav-item .nav-number { margin-left: auto; font-size: 10px; opacity: .65; font-variant-numeric: tabular-nums; }
  .sidebar-bottom { margin-top: auto; padding: 24px 8px 0; }
  .storage-label { display: flex; gap: 7px; align-items: center; font-size: 12px; color: #56665c; }
  .storage-label svg { width: 15px; height: 15px; }
  .config-path { display: block; margin-top: 9px; font: 11px/1.8 Consolas, monospace; color: #768278; overflow-wrap: anywhere; }
  .exit-btn { background: none; border: 0; color: #6d7971; margin-top: 24px; padding: 5px 0; font-size: 12px; display: flex; gap: 8px; align-items: center; }
  .exit-btn:hover { color: #ab453e; }
  .exit-btn svg { width: 16px; height: 16px; }
  .main { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
  .page-header { display: flex; align-items: center; justify-content: space-between; padding: 25px 40px; background: #fff; border-bottom: 1px solid var(--line); gap: 16px; }
  h1 { font-size: 21px; font-weight: 650; letter-spacing: .5px; }
  .page-header p { font-size: 12px; color: var(--muted); margin-top: 3px; }
  .header-tools { display: flex; align-items: center; gap: 14px; }
  .local-badge { display: inline-flex; gap: 6px; align-items: center; white-space: nowrap; padding: 4px 9px; color: var(--accent); border: 1px solid #dce9df; background: #f6faf6; border-radius: 5px; font-size: 11px; }
  .local-badge::before { content: ""; width: 5px; height: 5px; border-radius: 50%; background: var(--accent); }
  .close-btn { width: 32px; height: 32px; border: 1px solid var(--line); border-radius: 7px; background: white; color: var(--muted); font-size: 21px; line-height: 1; }
  .close-btn:hover { background: #f0f4f0; color: var(--ink); }
  .form { display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .panel-scroll { overflow-y: auto; padding: 28px 40px 32px; flex: 1; scrollbar-gutter: stable; }
  #config-fields { border: 0; min-width: 0; max-width: 900px; margin: 0 auto; }
  .section-heading { margin-bottom: 22px; display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
  h2 { font-size: 22px; font-weight: 650; letter-spacing: -.4px; }
  .section-heading p { color: var(--muted); margin-top: 5px; font-size: 13px; }
  .section-index { font-size: 11px; color: #829087; letter-spacing: 1px; padding-top: 8px; white-space: nowrap; }
  .settings-card { border: 1px solid var(--line); background: #fff; border-radius: 12px; margin-bottom: 18px; overflow: hidden; box-shadow: 0 2px 4px #263b2d02; }
  .card-header { padding: 16px 22px; border-bottom: 1px solid #edf0ed; display: flex; align-items: center; gap: 10px; }
  .card-header svg { color: var(--accent); width: 18px; height: 18px; }
  h3 { font-size: 14px; font-weight: 600; }
  .card-header .tag { margin-left: auto; font-size: 10px; color: #7b8980; background: #f3f6f3; border-radius: 4px; padding: 2px 7px; }
  .card-body { padding: 22px; }
  .field + .field, .field + .row, .row + .field { margin-top: 22px; }
  .field > label, .field-label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 8px; }
  .req { color: #ac604e; margin-left: 4px; }
  .field-key { float: right; color: #87918b; font: 10px/2 Consolas, monospace; font-weight: normal; }
  input[type="text"], input[type="url"], input[type="number"] { width: 100%; min-width: 0; height: 42px; padding: 10px 12px; color: var(--ink); border: 1px solid #dce3de; background: #fcfdfc; border-radius: 6px; outline: none; }
  input.mono { font-family: Consolas, "Microsoft YaHei", monospace; font-size: 13px; }
  input::placeholder { color: #9aa49d; }
  input:hover { border-color: #b3c4b8; }
  input:focus { border-color: var(--accent); background: white; box-shadow: 0 0 0 3px #32745e13; }
  input[aria-invalid="true"] { border-color: #bc554c; background: #fffafa; }
  .help { margin-top: 7px; color: var(--muted); font-size: 11px; line-height: 1.8; }
  code { font-family: Consolas, monospace; font-size: 11px; color: var(--accent); background: #f0f5f1; padding: 2px 5px; border-radius: 3px; overflow-wrap: anywhere; }
  .row { display: flex; gap: 18px; }
  .row > .field { flex: 1; min-width: 0; margin-top: 0; }
  .unit-input { position: relative; }
  .unit-input input { padding-right: 44px; }
  .unit-input span { position: absolute; right: 12px; top: 11px; color: #87928b; font-size: 12px; pointer-events: none; }
  .choice-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
  .choice { position: relative; display: flex; gap: 10px; align-items: flex-start; border: 1px solid var(--line); border-radius: 8px; padding: 14px; cursor: pointer; }
  .choice:hover { border-color: #a7bdb0; }
  .choice:has(input:checked) { border-color: var(--accent); background: #f3f8f4; box-shadow: 0 0 0 1px #32745e08; }
  .choice input { margin-top: 4px; width: 14px; height: 14px; accent-color: var(--accent); flex-shrink: 0; }
  .choice strong { font-weight: 600; font-size: 12px; display: block; }
  .choice small { display: block; color: var(--muted); font-size: 11px; margin-top: 4px; line-height: 1.7; }
  #regex-field { margin-top: 18px; padding-top: 18px; border-top: 1px dashed var(--line); }
  .section-note { display: flex; align-items: flex-start; gap: 9px; color: #7b877f; font-size: 11px; padding: 1px 3px; }
  .section-note svg { width: 15px; height: 15px; margin-top: 2px; }
  .switch-row { display: flex; align-items: center; justify-content: space-between; gap: 24px; cursor: pointer; }
  .switch-row + .switch-row { border-top: 1px solid #edf0ed; margin-top: 18px; padding-top: 18px; }
  .field > label.switch-row { display: flex; margin-bottom: 0; font-weight: normal; }
  .switch-copy strong { display: block; font-size: 13px; font-weight: 600; }
  .switch-copy small { display: block; font-size: 11px; color: var(--muted); margin-top: 4px; }
  .switch { appearance: none; width: 36px; height: 21px; background: #ccd6cf; border: 1px solid transparent; border-radius: 20px; position: relative; flex-shrink: 0; cursor: pointer; }
  .switch::after { content: ""; position: absolute; width: 15px; height: 15px; background: white; border-radius: 50%; top: 2px; left: 2px; box-shadow: 0 1px 3px #0002; transition: transform .15s; }
  .switch:checked { background: var(--accent); }
  .switch:checked::after { transform: translateX(15px); }
  .action-bar { background: #fff; border-top: 1px solid var(--line); padding: 17px 40px; display: flex; justify-content: space-between; align-items: center; gap: 18px; }
  .save-info { min-width: 0; }
  #dirty-state { font-size: 12px; color: var(--muted); display: flex; gap: 7px; align-items: center; }
  #dirty-state::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: #adb9b0; flex-shrink: 0; }
  #dirty-state.dirty { color: #9a6d30; }
  #dirty-state.dirty::before { background: #c19147; }
  .save-hint { font-size: 10px; margin-top: 3px; color: #88928c; }
  .actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .btn { padding: 9px 16px; border: 1px solid transparent; border-radius: 6px; font-size: 12px; white-space: nowrap; }
  .btn-primary { background: var(--accent); color: white; box-shadow: 0 2px 3px #32745e16; font-weight: 600; }
  .btn-primary:hover { background: #285e4d; }
  .btn-ghost { background: white; border-color: var(--line); color: #64736a; }
  .btn-ghost:hover { background: #f3f6f3; }
  kbd { font: 10px "Segoe UI", sans-serif; opacity: .65; margin-left: 12px; }
  #status { font-size: 12px; line-height: 1.7; padding: 11px 40px; border-top: 1px solid var(--line); background: #f0f5f1; overflow-wrap: anywhere; }
  #status:empty { display: none; }
  #status.error { color: #a23e36; background: #fff4f2; border-color: #f0d8d2; }
  #status.success { color: var(--accent); }
  dialog { margin: auto; width: 380px; max-width: calc(100% - 32px); border: 1px solid var(--line); border-radius: 14px; padding: 26px; color: var(--ink); box-shadow: 0 20px 80px #1f332b30; }
  dialog::backdrop { background: #1c302c40; }
  dialog h2 { font-size: 18px; }
  dialog p { color: var(--muted); font-size: 13px; margin: 12px 0 24px; }
  dialog .actions { justify-content: flex-end; }
  @media (min-width: 1400px) { .page-header, .action-bar { padding-left: 54px; padding-right: 54px; } }
  @media (max-width: 1000px) {
    .app-shell { grid-template-columns: 188px minmax(0, 1fr); }
    .sidebar { padding-left: 14px; padding-right: 14px; }
    .page-header, .action-bar { padding-left: 24px; padding-right: 24px; }
    .panel-scroll { padding: 24px; }
    .field-key { display: none; }
    kbd { display: none; }
  }
  @media (max-width: 700px) {
    .app-shell { display: flex; flex-direction: column; }
    .sidebar { padding: 12px 20px; border-right: 0; border-bottom: 1px solid var(--line); flex-shrink: 0; }
    .sidebar { flex-direction: row; align-items: center; gap: 8px; }
    .brand, .nav-label, .storage-label, .config-path { display: none; }
    .sidebar-bottom { margin: 0; padding: 0; }
    .exit-btn { margin: 0; padding: 10px 4px; font-size: 0; }
    .nav-list { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px; flex: 1; }
    .nav-item { justify-content: center; padding: 9px 5px; font-size: 12px; gap: 6px; }
    .nav-number { display: none; }
    .nav-item svg { width: 16px; height: 16px; }
    .main { flex: 1; }
    .page-header { padding: 16px 20px; }
    .panel-scroll { padding: 22px 20px; }
    .action-bar { padding: 14px 20px; flex-wrap: wrap; gap: 12px; }
    #status { padding: 10px 20px; }
    .save-info { flex: 1; }
    .section-index, .local-badge { display: none; }
  }
  @media (max-width: 460px) {
    .row { flex-direction: column; gap: 18px; }
    .choice-grid { grid-template-columns: 1fr; }
    .card-body { padding: 18px; }
    .card-header { padding: 14px 18px; }
    .actions { gap: 7px; }
    .btn { padding: 9px 12px; }
    .save-info { flex-basis: 100%; }
    .action-bar > .actions { width: 100%; justify-content: flex-end; }
  }
  @media (prefers-reduced-motion: reduce) { *, *::after { transition: none !important; } }
</style>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4m-5-10 3 3-3 3m6 0h4"/></svg></span><span class="brand-name" title="$WINDOW_TITLE">$WINDOW_TITLE</span></div>
      <div class="nav-label">工作空间 / 设置</div>
      <nav class="nav-list" role="tablist" aria-label="配置分类" aria-orientation="vertical">
        <button type="button" class="nav-item" id="tab-service" role="tab" aria-controls="panel-service" aria-selected="true" tabindex="0" data-panel="service"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3m6 0h4"/></svg>服务连接<span class="nav-number">01</span></button>
        <button type="button" class="nav-item" id="tab-window" role="tab" aria-controls="panel-window" aria-selected="false" tabindex="-1" data-panel="window"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 22h8m-4-4v4M3 8h18"/></svg>窗口与行为<span class="nav-number">02</span></button>
        <button type="button" class="nav-item" id="tab-advanced" role="tab" aria-controls="panel-advanced" aria-selected="false" tabindex="-1" data-panel="advanced"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h7m4 0h5M4 17h3m4 0h9"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></svg>高级设置<span class="nav-number">03</span></button>
      </nav>
      <div class="sidebar-bottom">
        <div class="storage-label"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>配置仅保存在本机</div>
        <span class="config-path">$CONFIG_PATH</span>
        <button type="button" class="exit-btn" id="btn-exit" title="退出应用" aria-label="退出应用"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H4v16h5m5-12 4 4-4 4m-6-4h14"/></svg>退出应用</button>
      </div>
    </aside>
    <main class="main">
      <header class="page-header">
        <div><h1>应用配置</h1><p>让你的 Web 服务，以桌面应用的方式运行。</p></div>
        <div class="header-tools"><span class="local-badge">本地配置</span><button type="button" class="close-btn" id="btn-close" style="display: $CLOSE_DISPLAY;" onclick="closeConfigOverlay()" title="关闭配置页（Esc）" aria-label="关闭配置页">×</button></div>
      </header>
      <form id="config-form" class="form" novalidate autocomplete="off">
        <div class="panel-scroll">
          <fieldset id="config-fields">
            <section id="panel-service" role="tabpanel" aria-labelledby="tab-service">
              <div class="section-heading"><div><h2>连接你的服务</h2><p>从一条启动命令开始，剩下的交给 WebDesktop。</p></div><span class="section-index">01 / 03</span></div>
              <div class="settings-card">
                <div class="card-header"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 6 6 6-6 6m9 0h5"/></svg><h3>服务启动</h3><span class="tag">必填</span></div>
                <div class="card-body">
                  <div class="field"><label for="web_command">启动命令<span class="req">*</span><span class="field-key">web_command</span></label><input class="mono" type="text" id="web_command" required placeholder="例如：dsh.cmd web" spellcheck="false" aria-describedby="command-help"><p class="help" id="command-help">填写完整启动命令。程序路径包含空格时，请用双引号包裹。</p></div>
                  <div class="field"><label for="web_url">服务地址<span class="req">*</span><span class="field-key">web_url</span></label><input class="mono" type="url" id="web_url" required pattern="https?://.*" placeholder="http://127.0.0.1:3080" spellcheck="false" aria-describedby="url-help"><p class="help" id="url-help">用于检查服务是否就绪，也作为固定访问地址。支持 http:// 和 https://。</p></div>
                </div>
              </div>
              <div class="settings-card">
                <div class="card-header"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 13 4-4m-6 7-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4 0 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0)"/></svg><h3>打开方式</h3></div>
                <div class="card-body">
                  <div class="choice-grid" role="group" aria-label="访问地址来源">
                    <label class="choice"><input type="radio" name="url_source" value="fixed"><span><strong>使用固定地址</strong><small>服务就绪后，直接打开上方地址。</small></span></label>
                    <label class="choice"><input type="radio" name="url_source" value="log"><span><strong>从服务日志提取</strong><small>适合动态端口或带令牌的访问地址。</small></span></label>
                  </div>
                  <div class="field" id="regex-field" hidden><label for="url_log_regex">日志提取正则<span class="req">*</span><span class="field-key">url_log_regex</span></label><input class="mono" type="text" id="url_log_regex" placeholder="(https?://\S+)" spellcheck="false" aria-describedby="regex-help"><p class="help" id="regex-help">取首个匹配结果；有捕获组时取第 1 组。例如 <code>dsh web: (https?://\S+)</code>。上方服务地址仍用于健康检查。</p></div>
                </div>
              </div>
              <p class="section-note"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></svg>首次使用只需填写启动命令与服务地址，其他设置可保留默认值。</p>
            </section>
            <section id="panel-window" role="tabpanel" aria-labelledby="tab-window" hidden>
              <div class="section-heading"><div><h2>顺手的桌面体验</h2><p>自定义窗口外观，以及应用如何在后台运行。</p></div><span class="section-index">02 / 03</span></div>
              <div class="settings-card">
                <div class="card-header"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/></svg><h3>窗口外观</h3></div>
                <div class="card-body">
                  <div class="field"><label for="window_title">窗口标题<span class="field-key">window_title</span></label><input type="text" id="window_title" required placeholder="给你的应用起个名字"></div>
                  <div class="row">
                    <div class="field"><label for="window_width">窗口宽度</label><div class="unit-input"><input type="number" id="window_width" required min="1" step="1"><span>px</span></div></div>
                    <div class="field"><label for="window_height">窗口高度</label><div class="unit-input"><input type="number" id="window_height" required min="1" step="1"><span>px</span></div></div>
                  </div>
                  <p class="help">默认 1200 × 800 px；应用窗口最小显示尺寸为 800 × 600 px。</p>
                </div>
              </div>
              <div class="settings-card">
                <div class="card-header"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v8m-5-5a8 8 0 1 0 10 0"/></svg><h3>运行行为</h3></div>
                <div class="card-body">
                  <div class="field"><span class="field-label" id="close-action-label">关闭窗口时</span><div class="choice-grid" role="group" aria-labelledby="close-action-label">
                    <label class="choice"><input type="radio" name="close_action" value="minimize_to_tray"><span><strong>最小化到系统托盘</strong><small>保持运行，随时从托盘恢复。</small></span></label>
                    <label class="choice"><input type="radio" name="close_action" value="exit"><span><strong>退出程序</strong><small>关闭窗口即结束应用。</small></span></label>
                  </div></div>
                  <div class="field"><label class="switch-row" for="kill_on_exit"><span class="switch-copy"><strong>退出时终止服务</strong><small>退出应用时同时结束后台服务，最小化到托盘不受影响。</small></span><input class="switch" type="checkbox" role="switch" id="kill_on_exit"></label></div>
                </div>
              </div>
            </section>
            <section id="panel-advanced" role="tabpanel" aria-labelledby="tab-advanced" hidden>
              <div class="section-heading"><div><h2>更多掌控，恰到好处</h2><p>调整目录、启动等待与调试选项；通常无需修改。</p></div><span class="section-index">03 / 03</span></div>
              <div class="settings-card">
                <div class="card-header"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7V5h7l2 2h9v13H3z"/></svg><h3>文件与目录</h3></div>
                <div class="card-body">
                  <div class="field"><label for="working_dir">工作目录<span class="field-key">working_dir</span></label><input class="mono" type="text" id="working_dir" required spellcheck="false" aria-describedby="working-help"><p class="help" id="working-help">启动命令的执行目录。支持用 ~ 表示用户主目录。</p></div>
                  <div class="field"><label for="log_dir">日志目录<span class="field-key">log_dir</span></label><input class="mono" type="text" id="log_dir" required spellcheck="false" aria-describedby="log-help"><p class="help" id="log-help">保存服务日志与程序日志，排查启动问题时可在此查看。</p></div>
                </div>
              </div>
              <div class="settings-card">
                <div class="card-header"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12h5l3-8 4 16 3-8h5"/></svg><h3>启动与健康检查</h3><span class="tag">单位：秒</span></div>
                <div class="card-body"><div class="row">
                  <div class="field"><label for="startup_timeout">启动超时</label><div class="unit-input"><input type="number" id="startup_timeout" required min="0.01" step="any" aria-describedby="startup-help"><span>秒</span></div><p class="help" id="startup-help">等待服务就绪的最长时间</p></div>
                  <div class="field"><label for="check_interval">检查间隔</label><div class="unit-input"><input type="number" id="check_interval" required min="0.01" step="any" aria-describedby="interval-help"><span>秒</span></div><p class="help" id="interval-help">两次健康检查之间的间隔</p></div>
                  <div class="field"><label for="check_timeout">单次检查超时</label><div class="unit-input"><input type="number" id="check_timeout" required min="0.01" step="any" aria-describedby="timeout-help"><span>秒</span></div><p class="help" id="timeout-help">每次请求的最长等待时间</p></div>
                </div></div>
              </div>
              <div class="settings-card">
                <div class="card-header"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/><path d="m8 12 3 3 5-6"/></svg><h3>调试与隐私</h3></div>
                <div class="card-body">
                  <label class="switch-row" for="show_console"><span class="switch-copy"><strong>显示服务控制台</strong><small>启动时显示命令行窗口，方便查看服务输出。</small></span><input class="switch" type="checkbox" role="switch" id="show_console"></label>
                  <label class="switch-row" for="private_mode"><span class="switch-copy"><strong>无痕模式</strong><small>默认关闭；开启后原生右键菜单可能变深色。</small></span><input class="switch" type="checkbox" role="switch" id="private_mode"></label>
                  <p class="help">两种模式均在正常退出时清理浏览器数据；修改后重启生效。</p>
                </div>
              </div>
            </section>
          </fieldset>
        </div>
        <div id="status" role="status" aria-live="polite" aria-atomic="true"></div>
        <footer class="action-bar">
          <div class="save-info"><span id="dirty-state">尚未修改</span><p class="save-hint">保存后将自动重启应用</p></div>
          <div class="actions"><button type="button" class="btn btn-ghost" id="btn-reset" disabled>还原修改</button><button type="submit" class="btn btn-primary" id="btn-save"><span id="save-label">保存并重启</span><kbd>Ctrl S</kbd></button></div>
        </footer>
      </form>
    </main>
  </div>
  <dialog id="discard-dialog" aria-labelledby="discard-title" aria-describedby="discard-description">
    <h2 id="discard-title">放弃未保存的更改？</h2><p id="discard-description">当前修改尚未保存，放弃后无法恢复。</p>
    <div class="actions"><button type="button" class="btn btn-ghost" id="discard-cancel" autofocus>继续编辑</button><button type="button" class="btn btn-primary" id="discard-confirm">放弃更改</button></div>
  </dialog>
  <script>
    var CONFIG = $CONFIG_JSON;
    var BRIDGE_READY_TIMEOUT_MS = 10000;
    var BRIDGE_POLL_INTERVAL_MS = 200;
    var form = document.getElementById('config-form');
    var fields = document.getElementById('config-fields');
    var tabs = Array.from(document.querySelectorAll('[role="tab"]'));
    var saving = false;
    var initialPayload;
    var pendingDiscard;
    var discardDialog = document.getElementById('discard-dialog');

    // srcdoc 遮罩与父页面同源，桥接在父窗口；首次配置时桥接在当前窗口。
    function resolveBridgeApi() {
      var api = window.pywebview && window.pywebview.api;
      if (api && typeof api.save_config === 'function') { return api; }
      if (window.parent !== window && window.parent.pywebview) {
        var parentApi = window.parent.pywebview.api;
        if (parentApi && typeof parentApi.save_config === 'function') { return parentApi; }
      }
      return null;
    }

    function whenBridgeReady(callback) {
      var startedAt = Date.now();
      var finished = false;
      var timer;
      function cleanup() {
        finished = true;
        clearTimeout(timer);
        window.removeEventListener('pywebviewready', tryResolve);
        if (window.parent !== window) { window.parent.removeEventListener('pywebviewready', tryResolve); }
      }
      function tryResolve() {
        if (finished) { return; }
        clearTimeout(timer);
        var api = resolveBridgeApi();
        if (api) { cleanup(); callback(api); return; }
        if (Date.now() - startedAt >= BRIDGE_READY_TIMEOUT_MS) {
          cleanup();
          setStatus('页面桥接不可用，请在桌面应用中重试或重启应用。你的填写内容已保留。', 'error');
          setSaveEnabled(true);
          return;
        }
        timer = setTimeout(tryResolve, BRIDGE_POLL_INTERVAL_MS);
      }
      window.addEventListener('pywebviewready', tryResolve);
      if (window.parent !== window) { window.parent.addEventListener('pywebviewready', tryResolve); }
      tryResolve();
    }

    function selectPanel(name) {
      tabs.forEach(function (tab) {
        var active = tab.dataset.panel === name;
        tab.setAttribute('aria-selected', String(active));
        tab.tabIndex = active ? 0 : -1;
        document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
      });
      document.querySelector('.panel-scroll').scrollTop = 0;
    }
    tabs.forEach(function (tab, index) {
      tab.addEventListener('click', function () { selectPanel(tab.dataset.panel); });
      tab.addEventListener('keydown', function (event) {
        var next;
        if (event.key === 'ArrowDown' || event.key === 'ArrowRight') { next = (index + 1) % tabs.length; }
        if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') { next = (index + tabs.length - 1) % tabs.length; }
        if (event.key === 'Home') { next = 0; }
        if (event.key === 'End') { next = tabs.length - 1; }
        if (next === undefined) { return; }
        event.preventDefault();
        selectPanel(tabs[next].dataset.panel);
        tabs[next].focus();
      });
    });
    var compactLayout = window.matchMedia('(max-width: 700px)');
    function syncTabOrientation() {
      document.querySelector('[role="tablist"]').setAttribute('aria-orientation', compactLayout.matches ? 'horizontal' : 'vertical');
    }
    compactLayout.addEventListener('change', syncTabOrientation);
    syncTabOrientation();

    function setStatus(text, kind) {
      var el = document.getElementById('status');
      el.textContent = text || '';
      el.className = kind || '';
    }
    function isDirty() { return JSON.stringify(collectPayload()) !== initialPayload; }
    function updateDirtyState() {
      var dirty = isDirty();
      var el = document.getElementById('dirty-state');
      el.textContent = dirty ? '有未保存的更改' : '尚未修改';
      el.className = dirty ? 'dirty' : '';
      document.getElementById('btn-reset').disabled = saving || !dirty;
    }
    function setSaveEnabled(enabled) {
      saving = !enabled;
      fields.disabled = saving;
      form.setAttribute('aria-busy', String(saving));
      ['btn-save', 'btn-exit', 'btn-close'].forEach(function (id) { document.getElementById(id).disabled = saving; });
      document.getElementById('save-label').textContent = saving ? '正在保存…' : '保存并重启';
      updateDirtyState();
    }
    function confirmDiscard(action) {
      if (saving) { return; }
      if (!isDirty()) { action(); return; }
      pendingDiscard = action;
      if (!discardDialog.open) { discardDialog.showModal(); }
    }
    document.getElementById('discard-cancel').addEventListener('click', function () { discardDialog.close(); });
    document.getElementById('discard-confirm').addEventListener('click', function () {
      var action = pendingDiscard;
      pendingDiscard = null;
      discardDialog.close();
      if (action) { action(); }
    });
    discardDialog.addEventListener('close', function () { pendingDiscard = null; });
    function leaveConfig(method) {
      confirmDiscard(function () {
        var api = resolveBridgeApi();
        if (api) { api[method](); }
        else { setStatus('页面桥接不可用，请在桌面应用中操作。', 'error'); }
      });
    }
    function closeConfigOverlay() {
      if (document.getElementById('btn-close').style.display !== 'none') { leaveConfig('exit_config_page'); }
    }
    document.addEventListener('keydown', function (event) {
      if (discardDialog.open) { return; }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        if (!saving) { form.requestSubmit(); }
      }
      if (event.key === 'Escape') { closeConfigOverlay(); }
    });

    function setValue(id, value) {
      document.getElementById(id).value = (value === undefined || value === null) ? '' : value;
    }
    ['web_command', 'web_url', 'url_log_regex', 'working_dir', 'log_dir', 'startup_timeout', 'check_interval', 'check_timeout', 'window_title'].forEach(function (key) { setValue(key, CONFIG[key]); });
    var size = (CONFIG.window_size && CONFIG.window_size.length === 2) ? CONFIG.window_size : [1200, 800];
    setValue('window_width', size[0]);
    setValue('window_height', size[1]);
    ['show_console', 'kill_on_exit', 'private_mode'].forEach(function (key) { document.getElementById(key).checked = !!CONFIG[key]; });
    var urlSource = CONFIG.url_source === 'log' ? 'log' : 'fixed';
    var closeAction = CONFIG.close_action === 'exit' ? 'exit' : 'minimize_to_tray';
    document.querySelectorAll('input[name="url_source"]').forEach(function (input) { input.checked = input.value === urlSource; });
    document.querySelectorAll('input[name="close_action"]').forEach(function (input) { input.checked = input.value === closeAction; });
    function syncUrlRegexRequired() {
      var isLog = document.querySelector('input[name="url_source"]:checked').value === 'log';
      var input = document.getElementById('url_log_regex');
      input.required = isLog;
      document.getElementById('regex-field').hidden = !isLog;
      if (!isLog) { input.setCustomValidity(''); input.removeAttribute('aria-invalid'); }
    }
    syncUrlRegexRequired();
    function rememberValues() {
      fields.querySelectorAll('input').forEach(function (input) {
        input.defaultValue = input.value;
        input.defaultChecked = input.checked;
      });
      initialPayload = JSON.stringify(collectPayload());
    }
    rememberValues();
    function onEdit(event) {
      if (event.target.matches('input')) {
        event.target.setCustomValidity('');
        event.target.removeAttribute('aria-invalid');
      }
      syncUrlRegexRequired();
      setStatus('');
      updateDirtyState();
    }
    form.addEventListener('input', onEdit);
    form.addEventListener('change', onEdit);
    document.getElementById('btn-reset').addEventListener('click', function () {
      confirmDiscard(function () {
        form.reset();
        fields.querySelectorAll('input').forEach(function (input) { input.setCustomValidity(''); input.removeAttribute('aria-invalid'); });
        syncUrlRegexRequired();
        updateDirtyState();
        setStatus('已还原为本次打开时的配置。');
      });
    });

    function collectPayload() {
      return {
        web_command: document.getElementById('web_command').value,
        web_url: document.getElementById('web_url').value,
        url_source: document.querySelector('input[name="url_source"]:checked').value,
        url_log_regex: document.getElementById('url_log_regex').value,
        working_dir: document.getElementById('working_dir').value,
        startup_timeout: document.getElementById('startup_timeout').value,
        check_interval: document.getElementById('check_interval').value,
        check_timeout: document.getElementById('check_timeout').value,
        window_title: document.getElementById('window_title').value,
        window_size: [document.getElementById('window_width').value, document.getElementById('window_height').value],
        show_console: document.getElementById('show_console').checked,
        kill_on_exit: document.getElementById('kill_on_exit').checked,
        private_mode: document.getElementById('private_mode').checked,
        close_action: document.querySelector('input[name="close_action"]:checked').value,
        log_dir: document.getElementById('log_dir').value
      };
    }
    function focusInvalid(input) {
      selectPanel(input.closest('[role="tabpanel"]').id.replace('panel-', ''));
      input.setAttribute('aria-invalid', 'true');
      input.focus();
      input.scrollIntoView({ block: 'center' });
      input.reportValidity();
    }
    function validateForm() {
      var firstInvalid;
      fields.querySelectorAll('input').forEach(function (input) {
        input.setCustomValidity('');
        input.removeAttribute('aria-invalid');
        if (input.required && !input.value.trim()) { input.setCustomValidity('请填写此项，不能只包含空格。'); }
        if (!input.checkValidity()) {
          input.setAttribute('aria-invalid', 'true');
          if (!firstInvalid) { firstInvalid = input; }
        }
      });
      if (!firstInvalid) { return true; }
      setStatus('还有配置需要完善，已为你定位到第一个问题。', 'error');
      focusInvalid(firstInvalid);
      return false;
    }
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      if (saving || !validateForm()) { return; }
      var payload = collectPayload();
      setSaveEnabled(false);
      setStatus('正在保存配置，请稍候…');
      whenBridgeReady(function (api) {
        Promise.resolve().then(function () { return api.save_config(payload); }).then(function (result) {
          if (result && result.ok) {
            rememberValues();
            updateDirtyState();
            document.getElementById('dirty-state').textContent = '更改已保存';
            document.getElementById('save-label').textContent = '正在重启…';
            setStatus(result.message || '保存成功，程序即将自动重启…', 'success');
            setTimeout(function () {
              Promise.resolve().then(function () { return api.restart_app(); }).catch(function (error) {
                setSaveEnabled(true);
                setStatus('配置已保存，但自动重启失败，请手动重启应用：' + error, 'error');
              });
            }, 800);
          } else {
            setSaveEnabled(true);
            var message = (result && result.message) || '保存失败，请检查填写内容';
            setStatus(message, 'error');
            var invalid = Array.from(fields.querySelectorAll('input[id]')).find(function (input) { return message.indexOf(input.id) !== -1; });
            if (invalid) { invalid.setCustomValidity(message); focusInvalid(invalid); }
          }
        }).catch(function (error) {
          setStatus('保存失败，填写内容已保留：' + error, 'error');
          setSaveEnabled(true);
        });
      });
    });
    document.getElementById('btn-exit').addEventListener('click', function () { leaveConfig('exit_app'); });
  </script>
</body>
</html>
""")


def build_wait_page(target_url: str, window_title: str | None = None) -> str:
    """
    生成「等待服务启动」页面 HTML。

    Args:
        target_url: 目标服务地址，展示在页面上。
        window_title: 配置中的窗口标题。

    Returns:
        完整 HTML 字符串。
    """
    return WAIT_PAGE_TEMPLATE.substitute(
        TARGET_URL=html.escape(target_url),
        WINDOW_TITLE=html.escape((window_title or "").strip() or "WebDesktop"),
    )


def build_error_page(
    title: str, message: str, log_tail: str = "", max_log_lines: int = 30, *, window_title: str | None = None,
) -> str:
    """
    生成错误提示页 HTML。

    Args:
        title: 错误标题（如「服务启动超时」）。
        message: 错误说明文字。
        log_tail: 服务日志末尾内容，为空时隐藏日志区域。
        max_log_lines: 日志最多展示行数（仅用于标签展示）。
        window_title: 配置中的窗口标题。

    Returns:
        完整 HTML 字符串。
    """
    return ERROR_PAGE_TEMPLATE.substitute(
        ERROR_TITLE=html.escape(title),
        ERROR_MESSAGE=html.escape(message),
        LOG_DISPLAY="block" if log_tail else "none",
        LOG_LINES=max_log_lines,
        LOG_TAIL=html.escape(log_tail),
        WINDOW_TITLE=html.escape((window_title or "").strip() or "WebDesktop"),
    )


def build_config_page(config: dict, config_path: str = "", show_close: bool = False) -> str:
    """
    生成分组配置页面 HTML，并回填当前配置。

    Args:
        config: 当前配置字典（用于回填表单）。
        config_path: 配置文件路径（展示用），为空时自动获取。
        show_close: 是否显示右上角关闭按钮（遮罩式进入时显示，点击返回来源页面）。

    Returns:
        完整 HTML 字符串。
    """
    # 将 < 转义为 \u003c，避免 JSON 内容破坏 <script> 标签
    config_json = json.dumps(config, ensure_ascii=False).replace("<", "\\u003c")
    return CONFIG_PAGE_TEMPLATE.substitute(
        CONFIG_JSON=config_json,
        CONFIG_PATH=html.escape(config_path or get_config_path()),
        CLOSE_DISPLAY="block" if show_close else "none",
        WINDOW_TITLE=html.escape((config.get("window_title") or "").strip() or "WebDesktop"),
    )


# 关闭遮罩脚本：移除配置页遮罩节点，目标网页原样保留（不导航、不刷新）
CLOSE_OVERLAY_SCRIPT = (
    "(function () {"
    "  var el = document.querySelector('[data-wbd-config-overlay]');"
    "  if (el) { el.remove(); }"
    "})();"
)


def build_config_overlay_script(html_content: str) -> str:
    """
    生成「配置页遮罩」注入脚本：在目标网页 DOM 上挂载全屏 iframe 遮罩，
    配置页经 iframe srcdoc 加载（与父页面同源，可经父窗口调用 js 桥）。
    遮罩打开 / 关闭均不导航，目标网页状态完整保留。

    Args:
        html_content: 配置页完整 HTML。

    Returns:
        可交由 window.evaluate_js 执行的注入脚本字符串。
    """
    # JSON 序列化后的字符串可作为合法 JS 字面量嵌入（引号、换行均已转义）
    payload = json.dumps(html_content, ensure_ascii=False)
    return (
        "(function () {"
        "  'use strict';"
        "  var existing = document.querySelector('[data-wbd-config-overlay]');"
        "  if (existing) { existing.remove(); }"
        "  var host = document.createElement('div');"
        "  host.setAttribute('data-wbd-config-overlay', '1');"
        "  var shadow = host.attachShadow({ mode: 'open' });"
        "  shadow.innerHTML = '<style>'"
        "    + ':host { all: initial; }'"
        "    + '.overlay { position: fixed; inset: 0; z-index: 2147483646; }'"
        "    + 'iframe { width: 100%; height: 100%; border: none; display: block; background: #f7f9f7; }'"
        "    + '</style>'"
        "    + '<div class=\"overlay\"><iframe title=\"应用配置\"></iframe></div>';"
        "  (document.body || document.documentElement).appendChild(host);"
        "  var frame = shadow.querySelector('iframe');"
        "  frame.srcdoc = " + payload + ";"
        "})();"
    )
