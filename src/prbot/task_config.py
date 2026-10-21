"""Non-secret policy in config.json; execution credentials in the macOS keychain."""

import os
from dataclasses import dataclass, field

from prbot.config import ConfigError, read_settings
from prbot.tasks import repository_url

SECRET_KEYS = {"GITHUB_TOKEN", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN"}


def credential(name, value=None):
    from keyring.backends.macOS import Keyring

    try:
        backend = Keyring()
        if value is not None:
            backend.set_password("org.prbot.execution", name, value)
            return None
        return os.environ.get(name) or backend.get_password("org.prbot.execution", name)
    except Exception:
        raise ConfigError("执行凭据不可读取，请解锁钥匙串并重新配置。") from None


@dataclass(frozen=True)
class TaskConfig:
    allowed_users: frozenset = frozenset()
    allowed_repos: frozenset = frozenset()
    session_ttl: int = 900
    timeout: int = 1800
    concurrency: int = 1
    ci_timeout: int = 3600
    required_checks: tuple = ("test", "dmg")
    local_repos: dict = field(default_factory=dict)

    @property
    def enabled(self):
        return bool(self.allowed_users and self.allowed_repos)

    @classmethod
    def load(cls):
        settings = read_settings()

        def get(key, default):
            return os.environ.get(key, settings.get(key, default))

        try:
            users = frozenset(int(v) for v in str(get("PRBOT_ALLOWED_USERS", "")).split(",") if v)
            repos = frozenset(
                repository_url("https://github.com/" + v.strip())
                for v in str(get("PRBOT_ALLOWED_REPOS", "")).split(",")
                if v.strip()
            )
            values = [
                int(get(k, d))
                for k, d in (
                    ("PRBOT_SESSION_TTL", 900),
                    ("PRBOT_TASK_TIMEOUT", 1800),
                    ("PRBOT_TASK_CONCURRENCY", 1),
                    ("PRBOT_CI_TIMEOUT", 3600),
                )
            ]
            if any(v < 1 for v in values) or values[2] > 4:
                raise ValueError
            checks = tuple(
                v.strip()
                for v in str(get("PRBOT_REQUIRED_CHECKS", "test,dmg")).split(",")
                if v.strip()
            )
            if not checks:
                raise ValueError
            local = get("PRBOT_LOCAL_REPOS", {})
            if not isinstance(local, dict):
                raise ValueError
            return cls(users, repos, *values, checks, local)
        except (ValueError, TypeError):
            raise ConfigError(
                "任务配置错误：请检查允许用户、owner/repo、正整数超时和必需检查名。"
            ) from None
