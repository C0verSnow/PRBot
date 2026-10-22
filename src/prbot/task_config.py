"""Local workspace preference; all operational configuration lives in Infisical."""

import os
from pathlib import Path

from prbot.config import ConfigError


def validate_work_root(value):
    try:
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_dir() or not os.access(path, os.W_OK):
            raise ValueError
        return str(path.resolve())
    except (ValueError, TypeError, OSError):
        raise ConfigError("Choose a working directory writable by the current user.") from None


def select_work_root(value):
    from prbot.config import prepare_env_directory, save_settings

    path = validate_work_root(value)
    prepare_env_directory(Path(path) / "PRBot-workspaces")
    save_settings({"PRBOT_WORK_ROOT": path})
    from prbot.secrets import clear_cache

    clear_cache()
    return path
