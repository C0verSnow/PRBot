# PRBot v0.1.0

在 macOS 上常驻运行的 Telegram Bot。支持 `/id` 身份查询，以及 `/FinishIssue` 仓库需求收集、Codex 修改和 GitHub PR 交付。仓库任务默认关闭，配置凭据和允许范围后启用。

目标：Apple Silicon，macOS 14+。DMG 自带 Python 和依赖，用户无需安装 Python、pip 或配置 PATH。

## 安装和使用

1. 在本仓库 **Actions → Test and build macOS DMG → 成功的运行 → Artifacts** 下载 `PRBot-v0.1.0-macos-arm64-development`，解压获得 DMG 和手册。工件保留 30 天，过期后重新运行 CI。
2. 打开 DMG，把 `PRBot.app` 拖到 Applications。
3. 打开应用，选择“配置 Token”，输入 BotFather 提供的 Token；验证成功后保存到 macOS 钥匙串。
4. 选择“启动服务”，或“开启登录自启动”。向 Bot 发送 `/id`。

**当前交付为开发测试版：仅 ad hoc 签名，没有 Developer ID 签名及 Apple 公证。** Gatekeeper 可能阻止打开；请阅读 [使用手册](使用手册.md)。构建成功不代表已完成真实 Telegram 和目标设备验收，进度见 [验收记录](docs/验收记录.md)。

## 结构

```text
src/prbot/      配置、命令注册、Telegram 长轮询、SQLite 处理记录、launchd、原生配置界面
packaging/     PyInstaller app 描述与入口
scripts/       仅远端 CI 运行的 DMG 构建及安装包检查
tests/        身份、路由、配置、重连、幂等、停止与凭据保护测试
```

Token 不进入日志或 LaunchAgent。普通消息仅在等待任务输入的会话内处理；发给其他 Bot 的命令被忽略。回复成功后才持久化成功进度；发送超时有可能实际已送达，重试不保证绝对不重复。每条回复最多尝试 5 次，然后停机等待排查。明确的权限拒绝和无效消息保留失败记录并继续接收后续消息。

用户主动停止会卸载 LaunchAgent，防止服务自动拉起。不可恢复的 Token/轮询冲突错误正常退出，不被 launchd 重试；进程意外崩溃由 launchd 限速重启。

## 开发约定

禁止在本地执行编译、打包或可能触发编译的验证。本仓库在 feature 分支 push、PR 或手动触发 Actions 时，远端安装依赖、静态检查、pytest、构建 DMG，并检查挂载复制后的独立运行时。不要在本地运行 `build_dmg.sh` 或 `verify_dmg.sh`。

构建链参考 [PyInstaller](https://pyinstaller.org/en/stable/spec-files.html)；Telegram 使用 [Bot API 长轮询](https://core.telegram.org/bots/api#getupdates) 和 [python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/telegram.bot.html)。

## /FinishIssue 仓库任务（Issue #3）

新增多轮任务入口，`/id` 的使用方式不变。先在应用选择“选择工作路径”，指定统一存放任务的文件夹；新任务保存在其 `PRBot-workspaces` 子目录中。首次使用在应用选择“配置任务”：填写 GitHub Token、允许的 Telegram 用户 ID 和允许的仓库 `owner/repo`。凭据存入 macOS 钥匙串；允许范围等设置保存在权限为 600 的 `config.json`。Codex 复用本机已有的 Plus/ChatGPT 登录：界面选择“导入 Codex 登录”，或开发环境执行 `python -m prbot.main import-codex`。登录过期后先在 Codex 重新登录，再导入并重启 Bot。DMG 自带固定版本 SDK 和原生 Codex runtime，不要求安装 Python、pip、Node 或修改 PATH。

1. 发送 `/FinishIssue`（大小写均可，群内可带 `@Bot用户名`）。
2. 输入 `https://github.com/owner/repo`。
3. 输入具体需求，例如“创建 test.md，内容为 test done，并更新说明”。也可在仓库链接后直接附上需求。
4. 收到任务编号后继续使用 `/id`；执行在后台进行。`/cancel` 仅取消尚未提交的输入。
5. Bot 校验仓库，创建独立 clone 和 `feature/prbot-编号` 分支，记录起始 SHA；SDK 返回受限的文件修改方案，控制器检查路径、差异和需求覆盖，提交并推送草稿 PR。
6. 必需的远端检查全部成功后 PR 转为待审，返回最终结果。未结束保持 `validating`，失败保留草稿 PR 和原因。Bot 不自动合并。

无需创建 Issue。若需求中明确包含同仓库的 `https://github.com/owner/repo/issues/编号`，会保存其上下文，并在交付后单独关闭 Issue、报告实际结果。PR 链接暂不作为入口，任务来源模型保留了扩展字段。

配置项与限制见 [任务配置](docs/任务配置.md)。真实 Telegram 和安装设备验收进度见 [验收记录](docs/验收记录.md)。


### 使用工作目录中的 .env

在软件中先“选择工作路径”。软件会自动创建 `<所选路径>/PRBot-workspaces/`，
并生成 `.env.example` 和权限为 600 的 `.env`，已有文件不会覆盖。
选择“打开配置目录”即可定位 `.env`，按注释填写 Telegram Token；
使用仓库任务还要填写 GitHub Token、允许用户 ID 和仓库范围。
Codex 登录继续通过“导入 Codex 登录”保存到钥匙串，不放入 `.env`。
保存后重启服务。未选择工作路径时使用应用数据目录下的 `workspaces/.env`。
配置优先级为进程环境变量、工作目录 `.env`、界面设置或钥匙串、默认值。
更换工作路径后读取新目录的 `.env`，旧配置不会搬动。

启动检查只读取 Codex 账户，不强制刷新；账户缺失、超时、SDK 或运行时异常会显示分类信息。
启动成功不代表已验证模型额度或真实推理能力。


## 桌面主窗口（PR #4 界面更新）

打开 PRBot 后显示独立 macOS 窗口：左侧为 New、Projects、Artifacts、Code、Pinned、Chats and tasks，主区域保留标题及需求输入框。外观参考 Claude 网页；不提供 Chat/Cowork 切换、输入框加号、五个推荐入口、模型选择或语音功能。底部显示当前 macOS 用户名，点击可打开“设置与服务”。

设置按工作空间、连接与权限、后台服务、登录自启动分组。原有工作路径选择、配置目录访问、Telegram Token、GitHub 与允许范围、Codex 登录导入、服务启停重启、自启动和状态入口均在此处。Token 在系统隐藏输入框中填写，不回显已保存值；配置读取顺序和钥匙串机制保持原规则。服务状态区区分 launchd 加载与实际运行状态，启动操作结束仍需后台检查成功。

首页输入框用于整理任务，格式为 `https://github.com/owner/repo 具体需求`。点击右下箭头会验证配置的 Bot、将仓库链接和需求复制到系统剪贴板，并打开对应 Telegram Bot。**此时尚未提交任务。** 先发送 `/FinishIssue`，收到提示后粘贴已复制内容；Telegram 继续执行原有身份、仓库允许范围和去重校验。需求会进入系统剪贴板，窗口不保存草稿，当前不提供通用 AI 对话或直接桌面执行。

其余导航项显示当前版本的功能说明，任务列表与交付回执仍在 Telegram，代码结果在 GitHub PR。关闭窗口不停止后台服务。界面采用 React + TypeScript + Tailwind CSS，源码位于 `frontend/`。前端仅在远端 CI 通过 Vite 编译，生成的静态资源随 DMG 打包，以本地 WebKit 加载，不依赖网络网页、额外浏览器或本地 HTTP 服务。

远端 CI 对安装包执行真实 WebKit 加载、导航与设置交互、原生状态桥接及截图检查，产物包含 `PRBot-ui.png` 和 `PRBot-ui.json`。不进行本地编译、打包或源码测试。
