# WebDesktop 桌面启动器

> **基于 [FQXCS/web-desktop](https://github.com/FQXCS/web-desktop) (MIT) 二次开发。**  
> 本项目在 FQXCS/web-desktop 基础上扩展为多项目本地启动器，AI 辅助开发，135 个测试用例由 AI 生成。  
> 原始仓库：[FQXCS/web-desktop](https://github.com/FQXCS/web-desktop) | License: MIT

一个 Windows 桌面 GUI 程序：点击桌面图标启动后打开窗口，在后台执行 web 服务的启动命令；
服务启动期间窗口显示「正在启动服务」等待动画，服务就绪后自动跳转到配置的地址
（如 `http://127.0.0.1:3080`）。浏览器内核使用系统自带 **Edge（WebView2）**。

支持两种运行模式，由配置决定：

- **单服务模式**（`projects` 为空）：即上面的原始行为，配置 `web_command` + `web_url`；
- **多项目模式**（`projects` 非空）：一个启动器管理多个本地项目（含前后端多服务），
  项目卡片一键启动 / 停止 / 重启、实时日志面板、服务依赖顺序、托盘动态菜单。
  详见 [多项目模式](#多项目模式一个启动器管理多个本地项目)。

## 功能特性

- 后台启动任意 web 服务命令（可配置），无需命令行窗口
- 等待期间展示加载动画与计时，就绪后自动跳转目标地址
- 配置文件保存在 `~/.WebDesktop/config.json`，首次运行时自动创建
- 首次运行（或启动时发现任意参数为空）首页进入图形化配置页面：全部参数必填，
  除 `web_command` / `web_url` 外的高级设置默认折叠，保存后自动重启
- 启动失败 / 进程退出 / 超时均展示错误页，附服务日志尾部，支持「重试」「打开配置」
- 目标网页的右键使用 WebView2 内核菜单（浅色，刷新 / 另存为 / 打印 / 复制 / 粘贴 / 全选等，
  随点击位置变化），应用不再注入任何自绘菜单；菜单中隐藏了「返回 / 前进」（避免退回加载页）
  与「更多工具」子菜单，页面自身处理右键的区域仍按页面行为处理
- 同时恢复被 pywebview（`debug=False`）关闭的浏览器快捷键：`Ctrl+R` / `F5` 刷新、
  `Ctrl+F` 查找、`Ctrl+P` 打印、`Ctrl+±` 缩放；开发者工具保持关闭
- 以遮罩方式打开的配置页覆盖在目标网页上：不刷新页面、服务保持运行，
  右上角「✕」（或 Esc）关闭后目标网页原样保留；错误页进入的配置页同样带「✕」，
  关闭后返回错误页；首次启动的配置页无此按钮
- 跳转到目标网页后，右下角叠加一个可拖动的齿轮图标：单击（或 Enter / 空格）直接
  打开配置页，无悬浮菜单与气泡提示；按住拖动可移动到任意位置（拖动与单击以 4 像素
  位移阈值区分），位置写入页面本站存储，整页刷新后仍然保留（图标不会出现在等待页、
  错误页与配置页上）
- 应用启动后常驻系统托盘（右下角）：左键单击托盘图标打开主窗口；右键菜单可
  「打开主窗口」或「退出程序」
- 关闭窗口行为可在配置页设置：「最小化到系统托盘」（窗口隐藏、程序与服务继续
  后台运行，从托盘恢复或退出）或「退出程序」（关闭窗口即结束进程）
- 退出程序时自动清理服务进程树（可配置，最小化到托盘期间服务保持运行）
- 单文件 exe 分发，双击即用

## 目录结构

```
web-desktop/
├── main.py                # 程序入口
├── app/
│   ├── config.py          # 配置管理（~/.WebDesktop 下配置文件的创建/加载/校验/保存）
│   ├── controller.py      # 应用控制器（服务编排、页面切换、配置保存与重启）
│   ├── service.py         # web 服务子进程管理（启动/健康检查/日志/清理）
│   ├── pages.py           # 内置等待页、错误页与配置页 HTML
│   ├── toolbar.py         # 目标网页可拖动齿轮按钮注入脚本（默认右下角）
│   ├── tray.py            # 系统托盘（纯 Win32 API 实现：托盘图标与右键菜单）
│   ├── ui.py              # pywebview 窗口与 js 接口
│   ├── webview_settings.py # 恢复 WebView2 内核默认能力（右键菜单、浏览器快捷键）
│   └── paths.py           # 路径工具（兼容打包运行）
├── build.ps1              # 打包脚本（PyInstaller）
├── create_shortcut.ps1    # 桌面快捷方式脚本
└── requirements.txt       # Python 依赖
```

配置文件不随项目分发，首次运行时自动在用户家目录创建：
`~/.WebDesktop/config.json`（Windows 上即 `C:\Users\<用户名>\.WebDesktop\config.json`）。

其中 `~` 表示用户家目录，**从环境变量解析**：Windows 读取 `USERPROFILE`
（缺失时用 `HOMEDRIVE` + `HOMEPATH` 拼接），Linux/macOS 读取 `HOME`。

## 配置文件说明（~/.WebDesktop/config.json）

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `web_command` | 服务启动命令（完整命令行字符串，含空格的路径用双引号包裹） | 无默认值，必填 |
| `web_url` | 服务就绪后跳转的地址（也是健康检查地址） | 无默认值，必填 |
| `url_source` | 访问地址来源：`fixed` 直接使用 `web_url` / `log` 服务就绪后用正则从日志提取 | `fixed` |
| `url_log_regex` | `url_source` 为 `log` 时，从服务日志提取访问地址的正则（取第一个匹配，含捕获组则取第 1 组） | `(https?://\S+)` |
| `working_dir` | 服务进程工作目录 | `~/.WebDesktop/working` |
| `log_dir` | 日志目录（服务日志与程序日志） | `~/.WebDesktop/log` |
| `startup_timeout` | 等待服务就绪的超时秒数 | `60` |
| `check_interval` | 健康检查轮询间隔（秒） | `0.5` |
| `check_timeout` | 单次健康检查超时（秒） | `2` |
| `window_title` | 窗口标题 | `Web 桌面启动器` |
| `window_size` | 窗口尺寸 `[宽, 高]` | `[1200, 800]` |
| `show_console` | 是否显示服务控制台窗口（调试用） | `false` |
| `kill_on_exit` | 退出程序时是否终止服务进程 | `true` |
| `close_action` | 关闭窗口动作：`minimize_to_tray` 最小化到系统托盘 / `exit` 退出程序 | `minimize_to_tray` |

配置页面说明：

- 首次运行（配置文件不存在）或启动时发现任意参数为空，首页即进入配置页面
- `web_command`、`web_url`、`url_source`（访问地址来源）与 `url_log_regex`（日志提取正则）直接展示；其余参数为高级设置，默认折叠
- 全部参数均为必填项；除 `web_command` / `web_url` 外均有默认值
- 点击「保存」校验通过后自动重启应用并进入正常启动流程
- 服务启动失败时可在错误页点击「打开配置」修改配置
- 在目标网页上的入口是右下角的可拖动齿轮图标（单击即进入）：以遮罩方式打开配置页，
  不刷新页面、服务保持运行；错误页进入的配置页为整页切换。遮罩式配置页显示右上角
  「✕」按钮，点击关闭并返回来源页面；首次运行进入的配置页不显示该按钮

示例：启动 Node 服务并跳转 3080 端口：

```json
{
  "web_command": "node server.js",
  "web_url": "http://127.0.0.1:3080",
  "working_dir": "D:\\my-web-service"
}
```

> 提示：`web_command` 按 Windows 命令行规则解析，可执行文件路径含空格时请用
> 双引号包裹，例如 `"\"D:\\Program Files\\nodejs\\node.exe\" server.js"`。

## 运行方式（源码）

```powershell
pip install -r requirements.txt
python main.py
```

### 受限环境（无 pip / 沙箱 / 无管理员权限）下安装依赖

本机 Python 3.14 未带 pip，且受限环境下 `tempfile` 会因为
`os.mkdir(..., 0o700)` 建出的目录不可写而失败（pip 解包与构建都会踩到）。可行做法：

```powershell
$env:TEMP = "$PWD\.tmp"
$env:PYTHONPATH = "$PWD\.sitecustomize;$PWD\.site"
py -3.12 -m pip install --no-build-isolation --no-cache-dir `
    --target "$PWD\.site" -i https://mirrors.aliyun.com/pypi/simple/ pywebview pyinstaller
```

- 依赖装进项目内的 `.site` 目录（`.gitignore` 已忽略），`main.py` 启动时会自动把它加入 `sys.path`；
- `.sitecustomize/sitecustomize.py` 修掉上述 `mkdir(mode=0o700)` 的坑，
  必须用 `--no-build-isolation` 才能让这个补丁进入 pip 的构建子进程
  （否则像 `proxy_tools` 这类没有预编译 wheel 的包会构建失败）；
- 打包脚本 `build.ps1` 已内置同样的沙箱兼容处理。

## 打包为 exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
powershell -ExecutionPolicy Bypass -File create_shortcut.ps1   # 创建桌面快捷方式
```

打包产物：`dist\WebDesktop.exe`。配置文件无需随包分发，首次运行时自动创建。

### 验证打包产物（无界面自检）

`--windowed` 打包后没有控制台输出，用自检入口即可确认产物可用：

```powershell
.\dist\WebDesktop.exe --selftest        # 配置解析 + 项目启停 + 进程树清理
.\dist\WebDesktop.exe --selftest-gui    # 额外弹一个 3 秒自动关闭的窗口，验证 WebView2 渲染
```

- 结果写入 `<log_dir>\selftest.txt`，退出码 0 表示通过；
- 启动阶段就失败时，堆栈会写到 `<配置目录>\crash.log`——无控制台环境下这是唯一的线索；
- 注意：如果当前 shell 本身无法运行 GUI 程序（受限/沙箱环境常见），
  `--selftest` 仍可验证后端链路，但窗口相关的验证必须在正常桌面会话里做。

### 产物形态：单文件 / 目录

默认产出单文件 `dist\WebDesktop.exe`（自解压到临时目录后运行）。
加 `-OneDir` 可改为产出目录形态 `dist\WebDesktopDir\`——没有自解压步骤，
启动更快，且在不允许写临时目录的环境里也能直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1 -OneDir
```

应用图标使用项目根目录的 `app.ico`：打包时嵌入 exe（文件图标、桌面快捷方式图标），
运行时窗口标题栏与任务栏图标同样来源于它；替换图标只需替换 `app.ico` 后重新打包。

## 常见问题

- **跳转到 dsh web 类服务时报「dsh web authentication required; reopen the URL printed by dsh web」**：这类服务（`dsh --profile web`）不是裸 HTTP 服务，它需要一次「令牌换 cookie」认证——服务启动时会在日志打印一行带 `?token=...` 的地址（如 `dsh web: http://127.0.0.1:3080/?token=xxx`），浏览器访问该地址后才拿到会话 cookie。此时请把 `url_source` 设为 `log`，并把 `url_log_regex` 配置为能匹配这行地址的正则（默认 `(https?://\S+)` 即可，dsh 会打印带 token 的地址），服务就绪后会自动用日志里的真实地址跳转。同时建议给 `web_command` 加上 `--no-open`，避免 dsh 额外弹出一个系统浏览器。
- **WebView2 运行时缺失**：Windows 11 已内置；Windows 10 需安装
  [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。
- **服务启动失败**：错误页会展示服务日志末尾 30 行，可点击「打开配置」修改
  `web_command` 等参数，也可临时将 `show_console` 改为 `true` 观察服务真实输出。
- **一直等待不跳转**：确认 `web_url` 与服务的实际监听端口一致；健康检查以 HTTP
  响应为准，若服务不是 HTTP 协议请调整检查方式。
- **日志位置**：默认在 `~/.WebDesktop/log\` 目录下：`app.log`（程序日志）、
  `web_service.log`（服务日志），可通过 `log_dir` 修改。
  每次启动服务都会清空 `web_service.log` 后重新写入，避免日志无限累积，
  同时确保从日志提取访问地址（`url_source=log`）只会命中本次启动打印的地址。
- **如何恢复默认配置**：退出程序后删除 `~/.WebDesktop/config.json`，
  下次启动会自动重建并进入配置页面。

## 多项目模式（一个启动器管理多个本地项目）

`config.json` 的 `projects` 数组非空时进入多项目模式：主窗口是项目卡片列表，每个项目可
一键**启动 / 停止 / 重启**、**打开页面**、查看**实时日志**；托盘右键菜单也会动态列出各项目
的操作。`projects` 为空或缺省时，程序沿用原来的单服务模式，完全向后兼容。

### 快速开始

1. 复制示例配置到配置目录（Windows 用户家目录）：

   ```powershell
   copy config.projects.example.json $env:USERPROFILE\.WebDesktop\config.json
   ```

2. 按自己的项目修改 `projects` 数组（可执行文件必须是绝对路径、参数写成数组）。
3. 启动：源码运行 `python main.py`，或双击打包后的 `WebDesktop.exe`。

界面右上角有「打开配置」（用系统默认编辑器打开 `config.json`）与「重新加载配置」
（重新读取配置，需先停止全部项目；配置有问题时主界面顶部会列出具体问题）。

**启动中可取消**：项目启动期间，卡片上的「停止」按钮会变成「取消启动」并保持可用。
点击后编排线程在下一个检查点中止流程，并回滚已经启动的服务——不必等满 `readyTimeout`
（对启动慢的服务尤其重要）。

**依赖关系可视化**：配置了 `dependsOn` 的服务，卡片上会显示启动序号（拓扑排序结果）、
「依赖 xxx」标记，多服务项目底部还有一行「启动顺序：1 api → 2 web」，
让「谁先起、依赖谁」一眼可见；实际启动也严格按这个顺序，前置服务就绪后才启动后继。

**实时日志面板**：底部抽屉式日志，按服务分标签页，支持自动滚动 / 清空 / 收起（Esc）。
渲染默认使用随程序分发的 [xterm.js](https://github.com/xtermjs/xterm.js)（MIT，见
`app/web/THIRD_PARTY_LICENSES.txt`），原生支持 ANSI 颜色、选中复制与滚动缓冲；
若该资源缺失或初始化失败，会自动退回内置的轻量 `<pre>` 渲染（错误行标红），
日志功能不会因为前端资源问题而不可用。日志既实时推送（每 150 ms 批量），也会落盘到
`<log_dir>/projects/<项目>/<服务>.log`。

### projects 字段说明

| 字段 | 说明 |
| --- | --- |
| `id` | 项目唯一标识，只能含字母/数字/下划线/点/短横线（同时作为日志子目录名） |
| `name` | 显示名称 |
| `icon` | 可选。支持三种写法：本地文件绝对路径（如 `C:\\icons\\octop.png`）、`http(s)://` 图片地址、`data:` URL。本地文件由程序读成 data URL 交给页面（页面无法直接引用本地路径），支持 png/jpg/jpeg/gif/webp/bmp/ico/svg，单文件上限 512 KB；文件不存在、格式不支持或过大时自动回退为「项目名首字母」占位图标（程序日志里会写明原因） |
| `url` | 项目就绪后「打开页面」的地址 |
| `url_source` | `fixed` 使用 `url`；`log` 表示从服务日志里按正则提取地址（如带一次性 token 的地址） |
| `url_log_regex` | `url_source=log` 时的提取正则，默认 `(https?://\S+)` |
| `stop_on_failure` | 启动失败时是否自动停止本项目已启动的服务，默认 `false` |
| `autoOpen` | 项目就绪后是否自动打开页面（与原版「就绪即跳转」一致），默认 `true` |
| `openIn` | 页面打开方式：`main`（默认，在主窗口内打开并自动注入「返回启动器」按钮）/ `window`（另开窗口，**实验性**：实测在部分环境会导致主程序退出）/ `browser`（交给系统默认浏览器） |
| `services` | 服务数组，见下表 |

服务（`services[]`）字段：

| 字段 | 说明 |
| --- | --- |
| `name` | 服务名（项目内唯一，如 backend / frontend） |
| `exe` | 可执行文件**绝对路径**（如 `python.exe`、`node.exe`）。禁止 .cmd/.bat/.ps1 |
| `args` | 参数数组，逐项传递（含空格的路径无需转义） |
| `cwd` | 工作目录 |
| `env` | 追加或覆盖的环境变量；值为 `null` 表示删除该变量 |
| `port` | 监听端口（健康检查用） |
| `health` | 健康检查路径（如 `/health`）；留空表示请求根路径 |
| `readyWhen` | 就绪判定方式：`http`（默认，需 `port`）/ `port` / `log`（需 `readyRegex`）/ `process`（进程稳定存活 `readyDelay` 秒） |
| `readyRegex` | `readyWhen=log` 时匹配就绪标志的正则 |
| `readyTimeout` | 等待就绪超时秒数，默认 `60` |
| `dependsOn` | 前置服务名数组：前置服务**就绪后**才启动本服务 |
| `killOnExit` | 程序退出时是否终止该服务，默认 `true` |

> Python 服务会自动补上 `PYTHONUNBUFFERED=1`、`PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`
> （只被 Python 读取，对其它程序无副作用），避免中文日志乱码与「半天不出日志」。

### 多服务 + 依赖顺序示例（后端 8001 + 前端 3782）

```json
{
  "id": "<项目名>",
  "name": "<项目名>",
  "url": "http://127.0.0.1:3782",
  "services": [
    {
      "name": "backend",
      "exe": "C:\\path\\to\\project\\.venv\\Scripts\\python.exe",
      "args": ["-m", "<模块名>"],
      "cwd": "C:\\path\\to\\project",
      "port": 8001,
      "health": "/",
      "readyTimeout": 120
    },
    {
      "name": "frontend",
      "exe": "C:\\Sub\\node\\node.exe",
      "args": ["scripts/dev.mjs", "-p", "3782"],
      "cwd": "C:\\path\\to\\project\\web",
      "port": 3782,
      "readyTimeout": 180,
      "dependsOn": ["backend"]
    }
  ]
}
```

注意：这里直接调用 `node.exe` + 入口脚本，而不是 `npm run dev`——
`.cmd`/`.bat`/`npm run` 会弹控制台窗口并且无法可靠清理进程树，属于明令禁止的启动方式。

> **Python 项目请用 `<venv>\Scripts\python.exe` + `-m <模块>` 启动，
> 不要直接调用 venv 里的控制台脚本 exe**（如 `<项目启动脚本>`、`<WSGI/ASGI 服务器>`）：
> 这类 exe 常由 uv / pipx 生成的「跳板程序」实现，需要解析自身路径，
> 在部分环境下会直接失败并打印
> `error: uv trampoline failed to canonicalize script path`。
> 实测对照（Octop）：
>
> | 启动方式 | 结果 |
> | --- | --- |
> | `.venv\Scripts\<项目启动脚本> run` | ❌ uv trampoline failed to canonicalize script path |
> | `.venv\Scripts\python.exe -m octop run` | ✅ 正常启动，监听 8088 |

### 与项目硬约束的对应关系

| 约束 | 实现方式 |
| --- | --- |
| 杜绝任何 cmd 窗口 | 全部子进程 `shell=False` + `CREATE_NO_WINDOW`；配置校验直接拒绝 .cmd/.bat/.ps1 |
| 进程树完整清理 | 三道保险：`taskkill /F /T` → Windows 作业对象（内核级终止）→ `kill()` |
| 前端不得执行任意命令 | js_api 只接受「项目 id / 日志键」等白名单参数，命令一律来自 Python 侧配置 |
| 日志必须实时推送 | 子进程 stdout 按块读取 → 逐行回调 → 每 150ms 批量推送前端，同时落盘 |
| 只监听 127.0.0.1 | 启动器自身不开放任何 HTTP 端口；健康检查只请求 `127.0.0.1` |

### 便携模式（可选）

设置环境变量 `WEBDESKTOP_HOME` 可把配置目录从 `~/.WebDesktop` 换成任意目录
（`log_dir`、`working_dir` 的默认值一并迁移），适合 U 盘部署或隔离测试。

## DeepSeek Harness 鲸鱼娘图标

- https://github.com/fornarwhal/deepseek-whale-girl-icon
