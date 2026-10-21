"""Explicit local setup: import only the current Codex login into protected storage."""

import json
import os
from pathlib import Path

from prbot.config import ConfigError, save_settings
from prbot.task_config import TaskConfig, credential


def import_codex_login():
    path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    try:
        if path.stat().st_mode & 0o077:
            raise ConfigError("Codex auth.json 权限太宽，请先限制为 600。")
        value = path.read_text()
        auth = json.loads(value)
        if not auth.get("tokens") and not auth.get("OPENAI_API_KEY"):
            raise ValueError
    except (OSError, ValueError):
        raise ConfigError("未找到可复用的 Codex 登录，请先在 Codex 登录后重新导入。") from None
    credential("codex-login", value)


def execution_credentials():
    github = credential("GITHUB_TOKEN")
    auth = credential("codex-login")
    if not github or not auth:
        raise ConfigError("仓库任务已启用但缺少 GitHub 凭据或 Codex 登录；请在界面配置任务。")
    return github, auth


def configure_tasks(github_token, users, repos):
    # Validate policy before persisting it; secrets never go through save_settings.
    from prbot.tasks import repository_url

    try:
        if not github_token.strip() or not users.strip() or not repos.strip():
            raise ValueError
        for user in users.split(","):
            int(user.strip())
        for repo in repos.split(","):
            repository_url("https://github.com/" + repo.strip())
    except ValueError:
        raise ConfigError(
            "请填写 GitHub Token、数字用户 ID 和 owner/repo（多个值用逗号分隔）。"
        ) from None
    credential("GITHUB_TOKEN", github_token.strip())
    import_codex_login()
    save_settings({"PRBOT_ALLOWED_USERS": users, "PRBOT_ALLOWED_REPOS": repos})
    return TaskConfig.load()
