# AGENTS.md —— 后续 agent 工作手册

> 这是给「下一个接手这个项目的 AI / 人」看的操作手册。**动手前请先读本文件第 3 节**
> （这台机器的硬约束），否则会重复踩一批已经踩过的坑。用户可见的功能说明见 `README.md`；
> 完整的踩坑与决策记录见操作日志：
> `<操作日志文件路径>`（时间顺序，最新在末尾）。

## 0. 这个项目是什么

把 FQXCS/web-desktop 从「单服务启动器」扩展为**多项目本地启动器**：项目卡片、一键启停/重启、
就绪自动跳转、实时日志终端、依赖顺序、托盘动态菜单、单文件/目录两种打包形态。

关键路径（这四个位置别搞混）：

| 用途 | 路径 | 说明 |
| --- | --- | --- |
| 源码（开发用） | `<仓库根目录>` | 在 DSH 工作区内，**只能构建/测试，不能运行** |
| 可运行副本（交付） | `<交付目录>\` | **必须在工作区外**，桌面快捷方式「WebDesktop 启动器」指向它 |
| 用户配置 | `C:\Users\<用户名>\.WebDesktop\config.json` | 程序实际读取的配置；示例是仓库根的 `config.projects.example.json` |
| 程序日志 | `C:\Users\<用户名>\.WebDesktop\log\` | `app.log`（程序）、`projects\<项目>\<服务>.log`（服务原始输出）、`selftest.txt` |

两种运行模式：配置里 `projects` 非空 → 多项目模式；为空 → 沿用原单服务模式（向后兼容）。

## 0.1 目录约定：仓库 / 项目放哪（新增项目前先看这条）

**推荐：`C:\Projects\<仓库名>`**（本项目作者的既有习惯是 `C:\Sub\<名字>`，也可继续沿用；
两者都在工作区之外、无空格、无中文，运行环境健康）。**也可以**放 `D:\Projects\<仓库名>` 等其它本地盘。

| 可以放 | 不要放 | 原因 |
| --- | --- | --- |
| `C:\Projects\<名>`、`C:\Sub\<名>`、其它本地盘普通目录 | `<DSH 工作区>`（DSH 工作区） | 工作区整棵树带 Low 完整性标签，**从里面运行的程序会被降级**：写不了 `%TEMP%`、`%APPDATA%`、用户目录（详见 3.1） |
| 同上 | `C:\Program Files`、`C:\Windows` 下 | 需要管理员权限，venv/npm 会失败 |
| 同上 | OneDrive / 坚果云等同步盘 | `node_modules`、`.venv` 文件极多，同步会拖慢并产生冲突 |
| 同上 | 桌面、下载文件夹 | 易被误删、路径常含中文或空格 |
| 同上 | 网络盘 / 映射盘 | venv 与 npm 安装会慢到不可用 |
| — | 深层且含空格/中文的路径 | 部分工具链（node-gyp、旧版 setuptools）会踩坑 |

配套规则（照做能省很多事）：

1. **一个仓库一个目录**，`venv` / `node_modules` 就放在仓库内（工作区外没有标签问题，不必外置）。
2. 项目的**数据、日志、缓存尽量留在仓库内或仓库同级**，不要散落到 `%APPDATA%`；
   这样启动器不需要为它额外配 `env` 覆盖。
3. **不要「先在工作区里 clone/建环境、再搬出去」**：标签与受限 ACL 会跟着文件走，
   届时既改不掉也复制不干净（实测 8 万多个文件修不动），只能整树重新复制 + 重建环境。
4. 需要 DSH/AI 帮忙开发时：源码放哪都行，但**安装依赖、运行程序一律在工作区之外**；
   工作区只放构建与测试用的东西（如本仓库源码本身）。
5. 启动器配置里引用项目时一律写**绝对路径**（`exe`、`cwd`），位置换了只改配置即可。

## 1. 代码结构（改哪里）

| 模块 | 职责 | 注意 |
| --- | --- | --- |
| `app/process.py` | 单个服务：无窗口启动、四种就绪判定、按块读日志、进程树清理 | 进程树清理是「taskkill → Windows 作业对象 → kill」三道保险，**不要退回只靠 taskkill** |
| `app/projects.py` | 项目配置校验、依赖拓扑排序、启停编排（可取消）、状态派生 | 边沿触发用「已处理集合」，不要用「上次状态」；中间态 busy 不能消费边沿 |
| `app/logs.py` | 日志环形缓冲、增量偏移、待推送队列 | 推送协议靠 `offset` 连续性校验，前端会自愈重拉 |
| `app/icons.py` | 图标解析：本地路径 → data URL、缓存、失败回退 | 页面无法引用本地文件，必须走 data URL |
| `app/launcher.py` | 多项目控制器、推送线程、js_api 白名单桥、退出看门狗 | 业务逻辑不要挂在前端推送成功与否上；退出看门狗在正常退出超时后先移除托盘图标再强退 |
| `app/launcher_page.py` | 启动器界面（**内联 HTML 模板**）+ 第三方资源内联注入 | 页面用 `html=` 注入 → 不能引用外部文件；xterm 资源缺失必须有降级路径 |
| `app/tray.py` | 纯 Win32 托盘 + 动态菜单 | 零依赖实现，不要引入 pystray |
| `app/config.py` / `app/paths.py` | 配置加载/校验、路径与便携模式 | JSON 一律用 `utf-8-sig` 读（兼容记事本 BOM） |
| `main.py` | 模式分流、自检入口、崩溃落盘、单实例互斥 | `--selftest` / `--selftest --project <id>` / `--selftest-gui`；单实例互斥仅拦截 GUI 模式，自检模式不受限 |

前端是单个内联模板（`LAUNCHER_PAGE_TEMPLATE`），用 `__WBD_XXX__` 标记做替换注入
（不用 `string.Template`，因为模板里有大量 JS 的 `$`）。加第三方前端资源时必须：
① 放进 `app/web/`；② 由 `build_launcher_page()` 内联；③ build.ps1 带 `--add-data`；
④ 提供「资源缺失时降级」的代码路径与测试。

## 2. 在这个启动器里新增一个项目（标准流程）

### 第 1 步：确认「怎么启动」（决定性一步）

- **Python 项目**：`<venv>\Scripts\python.exe` + `["-m", "<模块>", ...]`。
  **禁止**调用 venv 里的控制台脚本 exe（如 `<项目启动脚本>`）：它们常是 uv/pipx 生成的「跳板程序」，
  在本机会直接失败（`uv trampoline failed to canonicalize script path`）。
  实测对照：`<项目启动脚本> run` ❌ / `python.exe -m <模块名> run` ✅。
- **Node 项目**：`<node.exe 绝对路径>` + 入口 JS（如 `web\scripts\dev.mjs -p 3782`）。
  **禁止** `npm run`、`.cmd`、`.bat`（会弹控制台窗口且无法可靠清理进程树）。
  `node.exe` 在 `<node.exe 绝对路径>`。
- 一律：`exe` 用绝对路径、参数写成数组（不用引号转义）、`cwd` 指项目根。

### 第 2 步：确认「项目放在哪」

**必须放工作区之外**（如 `C:\Sub\<项目名>`），原因见 3.1。
已经放在工作区里的项目要移出来，并且**必须重建 venv / node_modules**——
旧文件带着低完整性标签，改不掉也复制不掉。

### 第 3 步：定就绪判定

`readyWhen`：

| 取值 | 含义 | 必填 |
| --- | --- | --- |
| `http`（默认） | 轮询 `http://127.0.0.1:<port><health>`，能收到任何 HTTP 响应即就绪 | `port` |
| `port` | TCP 能连上 | `port` |
| `log` | 服务日志匹配 `readyRegex` | `readyRegex` |
| `process` | 进程稳定存活 `readyDelay` 秒 | — |

端口不确定时先 `process`，等服务能起来再换成 `http` 精确判定。

### 第 4 步：写进两处配置

`config.projects.example.json`（仓库内，给人看）与
`C:\Users\<用户名>\.WebDesktop\config.json`（程序实际读的）。字段含义见 README 的字段表；
最小可用例子：

```json
{
  "id": "my-app",
  "name": "我的应用",
  "icon": "C:\\icons\\my-app.png",
  "url": "http://127.0.0.1:9000",
  "openIn": "main",
  "autoOpen": true,
  "services": [
    {
      "name": "backend",
      "exe": "C:\\Sub\\my-app\\venv\\Scripts\\python.exe",
      "args": ["-m", "myapp.server"],
      "cwd": "C:\\Sub\\my-app",
      "port": 9000,
      "health": "/",
      "readyTimeout": 120
    },
    {
      "name": "frontend",
      "exe": "C:\\Sub\\node\\node.exe",
      "args": ["scripts\\dev.mjs", "-p", "9001"],
      "cwd": "C:\\Sub\\my-app\\web",
      "port": 9001,
      "readyTimeout": 180,
      "dependsOn": ["backend"]
    }
  ]
}
```

### 第 5 步：自检（不要靠肉眼看窗口）

```powershell
& '<交付目录>\WebDesktopDir.exe' --selftest --project my-app
# 结果写到 C:\Users\<用户名>\.WebDesktop\log\selftest.txt，退出码 0 = 通过
```

### 第 6 步：交用户验收

界面（窗口/托盘/终端渲染）**agent 无法自测**（见 3.2），必须让用户双击桌面快捷方式确认，
并让用户把异常截图或 `<配置目录>\log\app.log`、`crash.log` 发回来。

## 3. 这台机器的硬约束（务必先读）

### 3.2 agent 的 shell 启动不了任何 GUI 程序

- 窗口 / 托盘 / WebView2 / 终端渲染 → 只能由用户验收；
- agent 侧用 `--selftest` + 写文件的方式验证后端链路；
- 从 agent shell 启动 GUI 子进程还会挂住命令输出（子进程继承管道句柄）→ 用「写文件 + 另起命令读文件」的方式取结果。

### 3.4 Python 环境

- `python` 默认是 **3.14 且没有 pip**；一律用 `py -3.12`。
- 依赖装在仓库内 `.site`（`main.py` 启动时会自动加入 `sys.path`，`.gitignore` 已忽略）。
- 受限环境安装依赖的完整命令见 README「受限环境下安装依赖」，要点：
  `--target .site --no-build-isolation` + 国内镜像 + `PYTHONPATH=<项目>\.sitecustomize;<项目>\.site`。

### 3.5 PowerShell 是 5.1（不是 pwsh 7）

- **不支持三元运算符 `? :`**、`??`；
- `.ps1` 文件必须带 **UTF-8 BOM**，否则中文串会破坏语法（用编辑工具改完 `.ps1`，**必须补回 BOM**，
  命令见 README 或本文件第 4 节的 BOM 修复片段）；
- `$ErrorActionPreference='Stop'` 时，原生程序往 stderr 写任何内容都会被当成终止性错误
  （PyInstaller 打印一行 INFO 就会中断）→ 调用原生程序前后临时切 `'Continue'`。

### 3.7 临时目录与 mkdtemp 补丁

- 跑测试/装包前把 `$env:TEMP`/`$env:TMP` 指向 `<项目>\.tmp`；
- 本环境下 `os.mkdir(path, 0o700)` 建出的目录**不可写**，会让 `tempfile`、pip、unittest 一起炸；
  `.sitecustomize/sitecustomize.py` 把建目录的 mode 放宽为 0o777，是**必备补丁**（已被 `.gitignore` 忽略）。
  跑测试的完整环境变量：`TEMP/TMP=<项目>\.tmp`、`PYTHONPATH=<项目>\.sitecustomize;<项目>\.site`。

## 4. 日常命令（照抄）

```powershell
# 跑全部测试（135 个用例；必须带上面 3.7 的环境变量）
cd <仓库根目录>
$env:TEMP="$PWD\.tmp"; $env:TMP="$PWD\.tmp"; $env:PYTHONPATH="$PWD\.sitecustomize;$PWD\.site"
py -3.12 -m unittest tests.test_multi_project tests.test_launcher tests.test_config `
    tests.test_controller_startup tests.test_main tests.test_toolbar tests.test_webview_settings tests.test_icons

# 打包（默认单文件 exe，并自动复制到桌面发布目录）
powershell -ExecutionPolicy Bypass -File build.ps1
powershell -ExecutionPolicy Bypass -File build.ps1 -OneDir          # 目录形态（无自解压，推荐日常用）
powershell -ExecutionPolicy Bypass -File build.ps1 -OneDir -Release '<发布目录>'   # 自定义发布目录

# 自检（打包后验证后端链路；结果写 <配置目录>\log\selftest.txt）
& '<交付目录>\WebDesktopDir.exe' --selftest
& '<交付目录>\WebDesktopDir.exe' --selftest --project <项目ID>

# 补 .ps1 的 BOM（用编辑工具改过 .ps1 之后必做）
$p='build.ps1'
$t=[System.IO.File]::ReadAllText($p,(New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText($p,$t,(New-Object System.Text.UTF8Encoding($true)))

# git（本仓库已配置本地署名；提交不上传）
git add -A; git commit -m "fix: ..."; git log --oneline -3
```

## 5. 改动约定

1. **加配置项要三处同步**：`DEFAULT_CONFIG`、`get_config_issues`/解析校验、README 字段表 + 示例配置。
2. 新增前端能力必须有**降级路径**（第三方资源缺失/初始化失败时功能不能不可用）。
3. 新增业务逻辑不要依赖「前端推送成功」——推送只是表现层，业务判定只看 Python 侧状态。
4. 任何"边沿触发"（自动打开页面、一次性提示）用**已处理集合**判定，并在回到初始态时清除该标记。
5. 写测试优先「真实子进程 / 真实端口 / 真实文件」，本项目已有先例（进程树心跳、HTTP 就绪、BOM 配置）。
6. JSON 读写统一 `utf-8-sig` 读、`utf-8` 无 BOM 写；`.ps1` 一律带 BOM。
7. 提交信息用中文、遵循仓库既有风格（`feat:` / `fix:` / `docs:`…），提交前清理调试产物。

## 6. 已知限制与后续方向

- **托盘图标残留**（2026-10-10 已修）：根因是非正常退出跳过 NIM_DELETE 且无单实例保护。
  修复为 P0 单实例互斥（main.py，仅拦 GUI 模式）+ P1 退出看门狗（launcher.py，
  正常退出超时 15s 后先 tray.stop() 再 os._exit(0)）。旧残留需用户重启 explorer 清一次。
- **依赖拓扑**目前是「启动序号 + 依赖胶囊 + 顺序链」的文字形态，可升级为 SVG 连线图。
- **日志终端**用 vendored 的 xterm.js 5.5.0（本地化，离线可用）；如需完整 VT 模拟可换完整版。
- `openIn: "window"`（运行期另开窗口）**本机实测会让主进程直接退出**，默认已改为 `main`
  （主窗口内打开 + 注入「返回启动器」）；若要修，需先解决 pywebview 运行期多窗口的稳定性。
- 未实现（按价值排序）：项目分组/搜索、日志文件滚动清理、启动完成桌面通知、
  项目级健康检查详情面板。**图形化配置编辑有意不做**——那等于让前端可写命令，违背安全约束。
- 单文件形态在受限环境（不能写临时目录）里无法自解压，日常请用 `-OneDir` 目录形态。

## 7. 交付前自检清单

- [ ] `py -3.12 -m unittest ...` 全绿（当前基线：135 个用例）
- [ ] 打包成功，且 `--selftest`（必要时 `--project <id>`）退出码为 0
- [ ] 可运行副本已复制到**工作区外**（`build.ps1` 会自动做）
- [ ] 示例配置与 `C:\Users\<用户名>\.WebDesktop\config.json` 已同步
- [ ] README / 本文件中的字段表与行为描述已随改动更新
- [ ] 操作日志已追加（结论、踩到的坑、待办）
- [ ] 需要用户肉眼验收的部分已明确告知用户
*（内容由AI生成，仅供参考）*
