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
