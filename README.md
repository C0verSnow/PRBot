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
6. Choose Issue or Pull Request, enter the link and a non-empty requirement (up to 12,000 characters), then Send. Acceptance, task state, results and recovered history appear in the window. Each mode retains its draft while switching tabs.

[使用手册](使用手册.md) · [Configuration and migration](docs/任务配置.md) · [Verification record](docs/验收记录.md)

## Execution and persistence

- The SDK explicitly uses `Sandbox.full_access` / `danger-full-access` and `ApprovalMode.deny_all` / `never`. Host commands and tools are enabled. The old read-only file-proposal executor has been removed; the SDK alone owns edits, commits and PR delivery.
- Applicable parent `AGENTS.md` rules are supplied. PRBot instructs tasks to avoid local compilation and to use existing remote CI. Repository and user requirements remain part of the task context.
- Request IDs are durable and idempotent in a local SQLite journal. Messages must be saved to Upstash before acceptance. A failed result archive retains `save_pending`; Retry saves the result without invoking Codex again. Interrupted tasks require human review and are never automatically re-executed.
- Dialogue payloads share a **10,000,000 byte** budget across all actors and channels in the `prbot:history:v1` namespace. Atomic Redis Lua checks and writes prevent concurrent overrun. The budget counts UTF-8 message JSON and message identifiers; Redis implementation overhead and unrelated database keys are outside this application data budget. At capacity, new messages are rejected with `Message not saved. Retry.` No silent eviction or expiry occurs. Use a dedicated Redis database for PRBot.
- History uses stable message IDs, source/actor separation and cursor pagination. Credentials are redacted before local journaling and remote archival. Bootstrap secrets, Telegram/Redis tokens, access tokens and refresh tokens are not returned by the bridge. The explicit ID token editor is the only credential display.
- Background Service manually starts/stops Telegram polling in the current login session. Health reports older than 15 seconds are marked Stale. Desktop tasks run in the window process so they use its ID token edits; closing the window interrupts them. Telegram tasks owned by a manually running background service continue. There is no login startup option or persistent launchd registration.

Task results record Codex's code-completion report, the remotely observed PR state, target Issue state and required CI result separately. `Delivered · verification incomplete` preserves the PR link when checks are pending, failed or unreadable; it is not full acceptance. Verification is a timestamped task-result snapshot, not continuous monitoring of GitHub. Existing historical results are retained unchanged. Only explicit submission shows `Submitting…`; background refreshes do not change the Send label.

## Development and verification

**Do not build, compile, run source tests or smoke scripts locally.** Local review is limited to reading, static lint/format checks and `git diff --check`. Existing GitHub Actions performs Python tests, real SDK runtime initialization, TypeScript checks, frontend build, native WebKit interaction checks, DMG packaging and installed bundle verification on remote macOS runners.

Real provider acceptance (Infisical, Upstash, paid SDK turn and GitHub delivery) must be recorded separately from mocked regression tests. Do not claim production readiness or close a task based only on UI controls or account lookup.
