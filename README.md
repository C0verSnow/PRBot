# PRBot

PRBot is a native macOS workbench for GitHub Issue and Pull Request tasks. Its React + TypeScript + Tailwind interface runs inside system WebKit. Desktop messages go directly to a shared application service and the Codex Python SDK; Telegram is an optional second input channel.

```text
Desktop / Telegram → shared task service → Codex (Full Access) → GitHub PR + remote CI
                          ↓
                    Upstash history
```

## Use

1. Download the DMG artifact from a successful [remote CI run](https://github.com/C0verSnow/PRBot/actions), move PRBot.app to Applications and open it. Current development builds use an ad hoc signature and are not notarized.
2. In Settings → Workspace, choose a working directory. That exact folder is the working root; no PRBot-specific subfolder or task-ID directory is created. Repositories use their repository names. Use Settings → Configuration to fill in and explicitly save the six Infisical bootstrap fields. Client Secret stays hidden; leave it blank to retain the saved value. The existing private `.env` can still be edited manually (`chmod 600`). Restart PRBot to apply changes to all services. The repository `.env` is the development bootstrap reference; packaged applications read the selected configuration directory. Operational settings never come from local Telegram/GitHub token fields.
3. In Infisical, add `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, and optionally `TELEGRAM_BOT_TOKEN` (`PR_BOT_TOKEN` is accepted for existing deployments). Desktop tasks do not require Telegram.
4. Sign in with Codex on the same Mac. Connections uses the complete `~/.codex/auth.json`. **Fetch Codex ID Token** reads only its native `tokens.id_token`, independently of Infisical, and fills the editable field. Fetch does not refresh, sign in, apply a manual override, or change native credentials. Failure preserves the current input. Choose **Use token** to apply the fetched or manually edited value. The access token and other login fields remain native. ID token edits are kept in the current window only. Normal SDK token refreshes update an unchanged native Codex sign-in; manual ID edits never overwrite it. No subscription tier check is performed.
5. GitHub uses the host Git credential provider, or `GITHUB_TOKEN` from Infisical. No `gh` installation is required. The account must have the target repository permissions needed for the requested changes.
6. Choose Issue or Pull Request, enter the link and a non-empty requirement (up to 12,000 characters), then Send. Live execution feedback and the final result appear above the input. Waiting tasks show an answer field that continues the same task. Browse saved conversations in the separate Chat History view, with all conversations selected initially, session previews and multiline message cards. New private chat opens a separate composer without a GitHub link. Each mode retains its draft while switching tabs.

[使用手册](使用手册.md) · [Configuration and migration](docs/任务配置.md) · [Verification record](docs/验收记录.md)

## Execution and persistence

- GitHub tasks explicitly use `Sandbox.full_access` / `danger-full-access` and `ApprovalMode.deny_all` / `never`. Host commands and tools are enabled. The old read-only file-proposal executor has been removed; the SDK alone owns edits, commits and PR delivery.
- Private chat uses the SDK read-only sandbox, receives no GitHub token, and is never automatically published to GitHub. Its history scope is separate from Issue / Pull Request tasks.
- Applicable parent `AGENTS.md` rules are supplied. PRBot instructs tasks to avoid local compilation and to use existing remote CI. Repository and user requirements remain part of the task context.
- Request IDs are durable and idempotent in a local SQLite journal. Messages must be saved to Upstash before acceptance. A failed result archive retains `save_pending`; Retry saves the result without invoking Codex again. Interrupted tasks require human review and are never automatically re-executed.
- Dialogue payloads share a **10,000,000 byte** budget across all actors and channels in the `prbot:history:v1` namespace. Atomic Redis Lua checks and writes prevent concurrent overrun. The budget counts UTF-8 message JSON and message identifiers; Redis implementation overhead and unrelated database keys are outside this application data budget. At capacity, new messages are rejected with `Message not saved. Retry.` No silent eviction or expiry occurs. Use a dedicated Redis database for PRBot.
- History uses stable message IDs, source/actor separation and cursor pagination. Credentials are redacted before local journaling and remote archival. Bootstrap secrets, Telegram/Redis tokens, access tokens and refresh tokens are not returned by the bridge. The explicit ID token editor is the only credential field and is masked. SDK progress forwards only allowlisted status and filtered assistant commentary; command output and reasoning stay internal.
- Background Service manually starts/stops Telegram polling in the current login session. Health reports older than 15 seconds are marked Stale. Desktop tasks run in the window process so they use its ID token edits; closing the window interrupts them. Telegram tasks owned by a manually running background service continue. There is no login startup option or persistent launchd registration.

Task results record Codex's code-completion report, the remotely observed PR state, target Issue state and required CI result separately. `Delivered · verification incomplete` preserves the PR link when checks are pending, failed or unreadable; it is not full acceptance. Verification is a timestamped task-result snapshot, not continuous monitoring of GitHub. Existing historical results are retained unchanged. Only explicit submission shows `Submitting…`; background refreshes do not change the Send label.

## Desktop interaction updates (Issue #11)

Connections opens cached data immediately and refreshes off the UI thread. Background status/task polling continues without locking navigation or inputs. Unchanged responses preserve React object identity; late responses are matched to their request. Failed refreshes keep the last usable data and show an error. Configuration drafts survive tab changes and background responses. Standard macOS Edit menu shortcuts support cut, copy, paste and select all in WebKit controls. Settings close uses a circular keyboard focus indicator.

The pinned Python SDK **0.163.0** supports `thread.turn(...).stream()`. PRBot uses versioned newline-delimited progress/text/result/error envelopes at the worker boundary. Questions use the existing structured `needs_clarification` outcome: a safe turn ends, PRBot persists the question, and an answer queues the **same task ID** with its original target, questions and answers. This is explicit context continuation in a fresh ephemeral SDK thread, not native paused-turn resumption. Credentials remain in temporary SDK homes, removed on exit/cancellation. Waiting tasks and events restore from the local journal; messages restore from Upstash.

See [implementation and verification evidence](docs/Issue11-桌面交互.md) for the seven requirements, screenshots and known limits.

## Development and verification

**Do not build, compile, run source tests or smoke scripts locally.** Local review is limited to reading, static lint/format checks and `git diff --check`. Existing GitHub Actions performs Python tests, real SDK runtime initialization, TypeScript checks, frontend build, native WebKit interaction checks, DMG packaging and installed bundle verification on remote macOS runners.

Real provider acceptance (Infisical, Upstash, paid SDK turn and GitHub delivery) must be recorded separately from mocked regression tests. Do not claim production readiness or close a task based only on UI controls or account lookup.

### Real provider acceptance

Manually dispatch the existing CI workflow on the feature branch with `acceptance=true`. The repository must have access to the organization's six `INFISICAL_*` Actions secrets. The runner reads `PRBOT_ACCEPTANCE_CODEX_AUTH` (the complete test account `auth.json`) from that Infisical environment alongside its Redis settings. An explicit Actions `PRBOT_ACCEPTANCE_CODEX_AUTH` or legacy JSON `PRBOT_ACCEPTANCE_BOOTSTRAP` remains supported. No local Codex login is uploaded.

Acceptance performs real SDK turns, checks a question survives reopening the service, submits an answer to the same task, and verifies that answer through an actual host file write. A CI-only HTTPS tunnel interrupts traffic to real Redis after SDK completion; the task must retain `save_pending`, then save and restore all four dialogue messages after reconnection without another SDK call. This checks client transport recovery, not a provider-wide outage or automatic UI connection monitoring. Only sanitized boolean evidence is uploaded; only this run's archive fields are removed.


## Issue #13：统一聊天与任务继续

Telegram 用 `/FinishIssue` 开始任务；收到澄清后回复通知，或发送 `/answer <task-id> <answer>`，会保留原任务继续执行。有多个等待任务时请明确选择。Infisical 的 `PRBOT_TELEGRAM_ACTOR_ID` 支持单个数字或逗号/空格分隔的多个数字 ID，授权最多缓存 60 秒，未知用户触发限流刷新，失败默认拒绝。

Chat History 可浏览本地及当前授权 Telegram 用户的消息，保留会话选择、刷新与分页。输入区上方显示当前 Codex 任务的真实状态；Telegram 连接状态仍在 Settings 中查看。Redis 历史上限为 10 MB，不自动删除；写入失败明确提示并保留重试。SQLite 和工作目录需一并备份，丢失 SQLite 后不能只靠 Redis 自动重建执行任务。完整数据模型、失败恢复和 SDK 新 Thread 继续语义见 [Issue #13 说明](docs/Issue13-统一任务交互.md)。


### Issue #15: readable history and local storage

Chat History opens the saved conversation browser, with loading, empty and retry states. Filter by Issue, Pull Request or private chat; select a titled conversation to load its messages from the server before pagination. New private chat explicitly opens a new question box. Issue/PR task selectors retain completed tasks from the latest 100 execution records; older messages remain available in history.

The desktop creates `~/Library/Application Support/PRBot/workbench.sqlite3` at startup, before cloud configuration is required. Settings → Workspace displays its full path. Tasks and events remain in this database; confirmed Redis messages and synchronized history also get a local SQLite copy. On connection failure, cached desktop messages remain readable with a warning. Without fresh cloud authorization, Telegram records stay hidden. Writes still require Redis confirmation; failed submissions remain retryable and are never labelled saved locally. Local copies are separated by Redis endpoint and retained across token rotation. Back up the application data directory and workspace together.

See [Issue #15 implementation and remote validation](docs/Issue15-历史与存储.md).


### Issue #17 故障恢复与输入保护

桌面执行器异常后会等待并重试，窗口会显示重连提示。提交后继续修改输入，受理回包只清除未变的字段，新内容会保留。桥接请求一般 60 秒超时，启动服务 90 秒；超时只结束前台等待，不取消后台工作，请先查看任务再重试。文件夹对话框最多等待 45 秒；目录失效时 Settings → Workspace 保留原路径并提示重新选择。

历史分页遇到 History changed 时，Retry loading 会重新加载首屏。成功轮询清除页面旧反馈，历史面板自己的加载错误仍保留到重试成功。Telegram 有待回答任务时，包含 GitHub 链接的新需求会提示使用 /FinishIssue，避免被当作答案；也可回复原通知或用 /answer 明确回答。工具提示不再混入 Issue 历史。Telegram 发送结果不明确时仍会重试，可能重复送达，这是保留的投递取舍。

逐项核查与处理见[问题清单](问题清单.md)顶部，执行和远端验证记录见 [tasklist.md](tasklist.md)。
