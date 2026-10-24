# Issue #15：历史记录与本地存储

以 Issue 最新评论为准，保留 Redis 跨端历史，增加 SQLite 本地副本。

## 已确认的问题

远端基线 [40075487383](https://github.com/C0verSnow/PRBot/actions/runs/40075487383) 执行 `python -m pytest -q --junitxml=test-results.xml`，224 项通过、2 项失败：All 汇总返回空列表；重启且 Redis 断连时无法读取已保存消息。旧历史默认只筛选私人聊天，空列表时没有说明，却一直显示提问框。

## 修改后的使用方式

- 历史默认 All，会话列表显示标题、来源和时间，完整消息按卡片显示并保留换行。
- 先在服务端按用户、模式和会话过滤，再分页；切换模式清除旧会话，不会因当前页缺少该会话而丢失选项。
- 加载中、没有记录和失败重试有独立提示；请求 ID 避免较晚到达的旧页面覆盖新选择。
- 私聊输入框通过 New private chat 打开；Issue/PR 保留最近 100 条任务供选择，Send 新建任务，Continue task 回答原任务。Telegram 任务保留只读状态。
- 桌面启动即创建原有 workbench.sqlite3，Settings 显示路径。数据库新增 history_messages、history_settings 表，不删改原有任务。
- Redis 确认保存后才写本地副本；读取云端历史时同步旧消息。重复写入从 Redis 读取原值补本地，防止不同重试内容覆盖原消息。
- 云端不可用时读取本地副本并提示。按 Redis 地址隔离，换 Token 保留数据；本地读取仍过滤用户和来源，没有新授权时不展示 Telegram 数据。

## 验证方式

不执行本地编译、构建或测试。只做源码、AST 和差异检查，使用现有远程 CI 跑 Python 回归、Ruff、TypeScript、前端构建、WKWebView 实际窗口和 DMG 安装检查。

新增回归覆盖 All、重启断网、本地副本用户与授权隔离、会话分页、Redis 地址隔离、Token 轮换、重复保存补副本以及初始化保留旧数据。窗口验收覆盖默认历史、卡片、换行、模式切换、分页、旧响应、失败重试、空状态和显式私聊入口。最终验收结果见下方记录。

## 边界

本地副本不是离线发送队列；新消息仍需 Redis 确认。未同步过的旧记录需要联网读取一次。历史浏览不会重建丢失的执行状态，也不会重新运行旧任务。任务状态选择器仍保留最近 100 条，完整已存聊天通过历史浏览。数据库与工作目录需一起备份；运行中备份应考虑 SQLite WAL。


## 代码入口

| 用户关心的改动 | 对应代码 |
| --- | --- |
| 会话列表、消息卡片、加载与重试、显式私聊 | `frontend/src/components/HistoryBrowser.tsx:7` |
| 已完成任务可选，任务反馈紧靠输入区 | `frontend/src/App.tsx:34` |
| 所有模式、用户隔离、会话分页、本地回退 | `src/prbot/conversation_service.py:256` |
| Redis 确认后落本地，重复写入恢复原值 | `src/prbot/conversation_store.py:79` |
| 云端历史同步到 SQLite | `src/prbot/conversation_store.py:131` |
| 无云端凭据时读取本人桌面历史 | `src/prbot/conversation_service.py:88` |
| SQLite 初始化和新增表 | `src/prbot/journal.py:25` |
| 桌面启动创建数据库与返回路径 | `src/prbot/desktop.py:99` |
| 设置页数据库位置 | `frontend/src/components/SettingsPanel.tsx:82` |

准确行号基于功能提交 `742ac4891007debff6d617b9cc82f29aca88890f`。


## 最终验收结果

[远端 CI 40084768706](https://github.com/C0verSnow/PRBot/actions/runs/40084768706) 对提交 `5e1c813b382e41515ad36dc27aa87db9b824b2a7` 全部通过：231 个 Python 测试、Ruff、TypeScript、前端构建、源码 WKWebView 窗口、DMG 构建与挂载复制、内置运行时、安装版 WKWebView 窗口和签名检查。

[source-ui.json](evidence/issue-15/source-ui.json) 与 [installed-ui.json](evidence/issue-15/installed-ui.json) 均为 `ok:true`，检查了常规窗口与最小窗口。完整截图、测试 XML 和开发版 DMG 可从该流程的 Artifacts 获取。没有执行本地编译、构建或测试，也未重新运行会消耗模型额度的真实提供商验收。

安装版第一轮曾在受理后清空草稿的 40 毫秒断言处失败；验收改为在有超时的条件等待后确认草稿为空，原检查仍保留，最终源码和安装版均通过。

[PR #16](https://github.com/C0verSnow/PRBot/pull/16) 已结束草稿状态，可审查；[Issue #15](https://github.com/C0verSnow/PRBot/issues/15) 已按任务要求关闭。PR 保持待合并。
