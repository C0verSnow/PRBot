import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

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
BOOTSTRAP_DEFAULTS = {
    "INFISICAL_URL": "https://app.infisical.com",
    "INFISICAL_ENVIRONMENT": "dev",
    "INFISICAL_SECRET_PATH": "/",
}


def validate_infisical_url(value):
    try:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError
        url.port  # Reject malformed or out-of-range ports.
    except (ValueError, TypeError):
        raise ConfigError(
            "INFISICAL_URL must be an HTTP(S) URL without credentials, query or fragment."
        ) from None
    return value


def bootstrap_values(configuration=None):
    values = {**BOOTSTRAP_DEFAULTS, **read_env(configuration)}
    values.update({k: v for k, v in os.environ.items() if k.startswith("INFISICAL_")})
    return values


def bootstrap_form(configuration=None):
    """Expose only non-secret fields and a boolean; never send the saved secret to WebKit."""
    values = bootstrap_values(configuration)
    return {
        "values": {
            k: values.get(k, "") for k in sorted(ENV_KEYS) if k != "INFISICAL_CLIENT_SECRET"
        },
        "client_secret_set": bool(values.get("INFISICAL_CLIENT_SECRET")),
        "environment_overrides": sorted(k for k in ENV_KEYS if k in os.environ),
        "path": str(env_path() if configuration is None else configuration),
    }


def save_bootstrap_form(values, expected_path):
    """Explicit, atomic update of the existing private bootstrap file.

    Blank client secrets retain the file's original value. Environment variables
    still win at runtime. Unknown keys/comments and the workspace preference stay intact.
    """
    path = env_path()
    if expected_path != str(path):
        raise ConfigError("Configuration directory changed. Reload Configuration before saving.")
    if not isinstance(values, dict) or set(values) != ENV_KEYS:
        raise ConfigError("Provide all six Infisical configuration fields.")
    cleaned = {}
    for key, value in values.items():
        if (
            not isinstance(value, str)
            or len(value) > 20000
            or any(ord(c) < 32 or ord(c) == 127 or c in "\x85\u2028\u2029" for c in value)
            or '"' in value
            or "'" in value
        ):
            raise ConfigError(key + " must be a single line without quotes or control characters.")
        cleaned[key] = value.strip()
    # Validate the persisted file and the effective configuration before touching disk.
    existing = read_env(path)
    keep_secret = not cleaned["INFISICAL_CLIENT_SECRET"]
    if keep_secret:
        cleaned["INFISICAL_CLIENT_SECRET"] = existing.get("INFISICAL_CLIENT_SECRET", "")
    effective = dict(cleaned)
    effective.update({k: v for k, v in os.environ.items() if k in ENV_KEYS})
    for key in sorted(ENV_KEYS):
        if not cleaned[key] and not effective.get(key):
            raise ConfigError(key + " is required.")
    for candidate in (cleaned, effective):
        if candidate["INFISICAL_URL"]:
            validate_infisical_url(candidate["INFISICAL_URL"])
        if candidate["INFISICAL_SECRET_PATH"] and not candidate["INFISICAL_SECRET_PATH"].startswith(
            "/"
        ):
            raise ConfigError("INFISICAL_SECRET_PATH must start with /.")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        original = path.read_text() if path.exists() else None
        lines = []
        for line in (original or "").splitlines():
            key = line.strip().partition("=")[0].strip()
            if key not in ENV_KEYS or (key == "INFISICAL_CLIENT_SECRET" and keep_secret):
                lines.append(line)
        lines.extend(
            key + "=" + cleaned[key]
            for key in sorted(ENV_KEYS)
            if key != "INFISICAL_CLIENT_SECRET" or not keep_secret
        )
        fd, temporary = tempfile.mkstemp(prefix=".prbot-env-", dir=path.parent)
        with os.fdopen(fd, "w") as out:
            out.write("\n".join(lines) + "\n")
            out.flush()
            os.fsync(out.fileno())
        # Do not overwrite a workspace switch or an external editor's newer file.
        if (
            env_path() != path
            or path.is_symlink()
            or ((path.read_text() if path.exists() else None) != original)
        ):
            raise ConfigError("Configuration changed while saving. Reload and retry.")
        os.replace(temporary, path)
    except (OSError, UnicodeError):
        raise ConfigError(
            "Cannot save Infisical configuration. Check directory write permissions and retry."
        ) from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return bootstrap_form(path)


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
