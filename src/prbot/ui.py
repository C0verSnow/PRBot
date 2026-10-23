"""Native workspace actions; application secrets are managed in Infisical."""

import asyncio
import subprocess

from prbot import service
from prbot.config import Config, ConfigError, prepare_env_directory
from prbot.main import validate_api

ACTIONS = {"choose_workspace", "open_configuration", "start_service", "stop_service"}


def applescript(source):
    result = subprocess.run(
        ["/usr/bin/osascript", "-"], input=source, capture_output=True, text=True, timeout=300
    )
    return result.stdout.strip() if result.returncode == 0 else None


def perform_action(action):
    if action not in ACTIONS:
        raise ConfigError("Unsupported action.")
    if action == "choose_workspace":
        from prbot.task_config import select_work_root

        path = applescript(
            'POSIX path of (choose folder with prompt "Choose a working directory for PRBot tasks")'
        )
        if path:
            select_work_root(path)
            return (
                "Working directory saved. New tasks use this folder; existing tasks stay in place."
            )
        return "Folder selection cancelled."
    if action == "open_configuration":
        path = prepare_env_directory()
        subprocess.run(["/usr/bin/open", "-R", str(path)], check=True)
        return "Edit the Infisical connection settings in .env, then refresh connections."
    if action == "stop_service":
        service.stop()
        return "Service stopped."
    config = Config.load()
    asyncio.run(validate_api(config.token))
    service.install()
    return "Starting service. Waiting for a health report."


def show():
    from prbot.desktop import show as show_window

    show_window()
