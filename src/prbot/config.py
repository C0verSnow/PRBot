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
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_TOKEN",
    "PRBOT_ALLOWED_USERS",
    "PRBOT_ALLOWED_REPOS",
    "PRBOT_LOG_LEVEL",
    "PRBOT_POLL_TIMEOUT",
    "PRBOT_AUTOSTART",
    "PRBOT_SESSION_TTL",
    "PRBOT_TASK_TIMEOUT",
    "PRBOT_TASK_CONCURRENCY",
    "PRBOT_CI_TIMEOUT",
    "PRBOT_REQUIRED_CHECKS",
}


def workspace_directory():
    selected = os.environ.get("PRBOT_WORK_ROOT") or read_settings().get("PRBOT_WORK_ROOT")
    if selected:
        selected = Path(selected).expanduser()
        if not selected.is_absolute() or not selected.is_dir():
            raise ConfigError("工作路径不可用，请在软件中重新选择工作路径。")
        folder = selected / "PRBot-workspaces"
    else:
        folder = data_dir() / "workspaces"
    if folder.is_symlink():
        raise ConfigError("工作目录不能是符号链接，请重新选择工作路径。")
    return folder


def env_path():
    return workspace_directory() / ".env"


def prepare_env_directory(folder=None):
    folder = workspace_directory() if folder is None else Path(folder)
    if folder.is_symlink():
        raise ConfigError("工作目录不能是符号链接，请重新选择工作路径。")
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
                raise ConfigError("配置文件不能是符号链接，请检查工作目录。")
            if not target.exists():
                with open(target, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
                    out.write(template)
        return folder / ".env"
    except OSError:
        raise ConfigError("无法准备 .env，请检查工作目录权限。") from None


def read_env():
    # Never search arbitrary task directories or inherit their credentials.
    path = env_path()
    if path.is_symlink():
        raise ConfigError(".env 不能是符号链接，请检查工作目录。")
    if not path.exists():
        return {}
    try:
        if (
            path.is_symlink()
            or not stat.S_ISREG(path.stat().st_mode)
            or path.stat().st_mode & 0o077
        ):
            raise ConfigError(".env 必须是普通文件且仅本人可读写，请执行 chmod 600 后重试。")
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
        raise ConfigError(".env 无法读取，请按 .env.example 检查 KEY=value 格式。") from None


def environment_value(key, default=None):
    if key in os.environ:
        return os.environ[key]
    return read_env().get(key, default)


def validate_token(token: str) -> str:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{30,100}", token):
        raise ConfigError("Token 缺失或格式错误，请在 PRBot 中配置 BotFather 提供的 Token。")
    return token


def keychain_token(value=None):
    # Select the native backend explicitly; do not fall back to plaintext storage.
    from keyring.backends.macOS import Keyring

    try:
        backend = Keyring()
        if value is None:
            return backend.get_password("org.prbot.telegram", "bot-token")
        backend.set_password("org.prbot.telegram", "bot-token", value)
    except Exception:
        raise ConfigError("钥匙串不可访问，请解锁登录钥匙串并允许 PRBot 访问。") from None


def read_settings():
    path = data_dir() / "config.json"
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise ConfigError("config.json 权限太宽，请执行 chmod 600 后重试。")
    try:
        result = json.loads(path.read_text())
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, OSError):
        raise ConfigError("config.json 无法读取，请修正 JSON 格式和文件权限。") from None


def save_settings(values):
    path = data_dir() / "config.json"
    # Only settings, never copy environment variables or a Token into this file.
    allowed = {
        k: v
        for k, v in values.items()
        if k not in {"TELEGRAM_BOT_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"}
    }
    old = read_settings()
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
    autostart: bool = False

    @classmethod
    def load(cls):
        settings = read_settings()

        def get(key, default=None):
            return environment_value(key, settings.get(key, default))

        token = environment_value("TELEGRAM_BOT_TOKEN") or settings.get("TELEGRAM_BOT_TOKEN")
        if not token:
            token = keychain_token()
        token = validate_token(token or "")
        level = str(get("PRBOT_LOG_LEVEL", "INFO")).upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ConfigError("PRBOT_LOG_LEVEL 只能是 DEBUG、INFO、WARNING、ERROR。")
        try:
            timeout = int(get("PRBOT_POLL_TIMEOUT", 30))
            if not 1 <= timeout <= 50:
                raise ValueError
        except (ValueError, TypeError):
            raise ConfigError("PRBOT_POLL_TIMEOUT 必须为 1 到 50 秒。") from None
        autostart = str(get("PRBOT_AUTOSTART", "false")).lower()
        if autostart not in {"true", "false"}:
            raise ConfigError("PRBOT_AUTOSTART 只能是 true 或 false。")
        return cls(token, level, timeout, autostart == "true")
