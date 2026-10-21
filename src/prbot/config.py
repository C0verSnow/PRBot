import json
import os
import re
from dataclasses import dataclass, field

from prbot.paths import data_dir


class ConfigError(Exception):
    pass


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
            return os.environ.get(key, settings.get(key, default))

        token = os.environ.get("TELEGRAM_BOT_TOKEN") or settings.get("TELEGRAM_BOT_TOKEN")
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
