# PRBot v0.1.0

在 macOS 上常驻运行的 Telegram Bot。目前只支持 `/id`，返回发送方 User ID、Chat ID 和 Chat Type。不执行 GitHub 或 Codex 任务。

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

Token 不进入日志或 LaunchAgent。普通消息和发给其他 Bot 的命令被忽略。回复成功后才持久化成功进度；发送超时有可能实际已送达，重试不保证绝对不重复。每条回复最多尝试 5 次，然后停机等待排查。明确的权限拒绝和无效消息保留失败记录并继续接收后续消息。

用户主动停止会卸载 LaunchAgent，防止服务自动拉起。不可恢复的 Token/轮询冲突错误正常退出，不被 launchd 重试；进程意外崩溃由 launchd 限速重启。

## 开发约定

禁止在本地执行编译、打包或可能触发编译的验证。本仓库在 feature 分支 push、PR 或手动触发 Actions 时，远端安装依赖、静态检查、pytest、构建 DMG，并检查挂载复制后的独立运行时。不要在本地运行 `build_dmg.sh` 或 `verify_dmg.sh`。

构建链参考 [PyInstaller](https://pyinstaller.org/en/stable/spec-files.html)；Telegram 使用 [Bot API 长轮询](https://core.telegram.org/bots/api#getupdates) 和 [python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/telegram.bot.html)。
