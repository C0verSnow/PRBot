import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from prbot.paths import data_dir


class ConfigError(Exception):
    pass


ENV_KEYS = {
    "INFISICAL_URL",
    "INFISICAL_CLIENT_ID",
    "INFISICAL_CLIENT_SECRET",
    "INFISICAL_PROJECT_ID",
    "INFISICAL_ENVIRONMENT",
    "INFISICAL_SECRET_PATH",
}


def workspace_directory():
    selected = os.environ.get("PRBOT_WORK_ROOT") or read_settings().get("PRBOT_WORK_ROOT")
    if selected:
        selected = Path(selected).expanduser()
        if not selected.is_absolute() or not selected.is_dir():
            raise ConfigError("Working directory unavailable. Choose a folder in Settings.")
        folder = selected / "PRBot-workspaces"
    else:
        folder = data_dir() / "workspaces"
    if folder.is_symlink():
        raise ConfigError("Working directory cannot be a symlink. Choose another folder.")
    return folder


def env_path():
    return workspace_directory() / ".env"


def prepare_env_directory(folder=None):
    folder = workspace_directory() if folder is None else Path(folder)
    if folder.is_symlink():
        raise ConfigError("Working directory cannot be a symlink. Choose another folder.")
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        source = (
            Path(sys._MEIPASS) / ".env.example"
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[2] / ".env.example"
        )
        template = source.read_text()
        for name in (".env.example", ".env"):
            target = folder / name
            if target.is_symlink():
                raise ConfigError(
                    "Configuration file cannot be a symlink. Check the working directory."
                )
            if not target.exists():
                with open(target, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
                    out.write(template)
        return folder / ".env"
    except OSError:
        raise ConfigError("Cannot prepare .env. Check working directory permissions.") from None


def read_env(configuration=None):
    # Never search arbitrary task directories or inherit their credentials.
    path = env_path() if configuration is None else Path(configuration)
    if path.is_symlink():
        raise ConfigError(".env cannot be a symlink. Check the working directory.")
    if not path.exists():
        return {}
    try:
        if (
            path.is_symlink()
            or not stat.S_ISREG(path.stat().st_mode)
            or path.stat().st_mode & 0o077
        ):
            raise ConfigError(".env must be a private regular file. Run chmod 600 and retry.")
        result = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key not in ENV_KEYS:
                continue  # Other tools may share the repository's .env.
            if not separator:
                raise ValueError
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError
                value = value[1:-1]
            if value:
                result[key] = value
        return result
    except (OSError, UnicodeError, ValueError):
        raise ConfigError(
            "Cannot read .env. Check KEY=value formatting against .env.example."
        ) from None


def environment_value(key, default=None):
    from prbot.secrets import load_secrets

    return load_secrets().get(key, default)


def validate_token(token: str) -> str:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{30,100}", token):
        raise ConfigError("Telegram token missing or invalid. Set TELEGRAM_BOT_TOKEN in Infisical.")
    return token


def read_settings():
    path = data_dir() / "config.json"
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise ConfigError("config.json permissions are too broad. Run chmod 600 and retry.")
    try:
        result = json.loads(path.read_text())
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, OSError):
        raise ConfigError(
            "Cannot read config.json. Check JSON formatting and file permissions."
        ) from None


def save_settings(values):
    path = data_dir() / "config.json"
    # Only the user's local workspace selection may be persisted.
    allowed = {k: v for k, v in values.items() if k == "PRBOT_WORK_ROOT"}
    old = read_settings()
    old = {k: v for k, v in old.items() if k == "PRBOT_WORK_ROOT"}
    old.update(allowed)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as out:
        json.dump(old, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    tmp.chmod(0o600)
    tmp.replace(path)


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    log_level: str = "INFO"
    poll_timeout: int = 30

    @classmethod
    def load(cls):
        from prbot.secrets import require_secrets

        values = require_secrets("TELEGRAM_BOT_TOKEN")
        token = validate_token(values["TELEGRAM_BOT_TOKEN"])
        level = str(values.get("PRBOT_LOG_LEVEL", "INFO")).upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ConfigError("PRBOT_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR.")
        try:
            timeout = int(values.get("PRBOT_POLL_TIMEOUT", 10))
            if not 1 <= timeout <= 10:
                raise ValueError
        except (ValueError, TypeError):
            raise ConfigError("PRBOT_POLL_TIMEOUT must be 1 to 10 seconds.") from None
        return cls(token, level, timeout)
