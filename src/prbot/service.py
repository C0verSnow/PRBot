import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from prbot.config import ConfigError
from prbot.paths import data_dir

LABEL = "org.prbot.agent"


def agent_path():
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def launchctl(*args, check=True):
    result = subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=20)
    if check and result.returncode:
        raise ConfigError(
            "launchd operation failed. Use a logged-in macOS session and check status."
        )
    return result


def domain():
    return f"gui/{os.getuid()}"


def executable():
    if getattr(sys, "frozen", False):
        path = Path(sys.executable).resolve()
        if not str(path).startswith(
            ("/Applications/PRBot.app/", str(Path.home() / "Applications/PRBot.app") + "/")
        ):
            raise ConfigError("Move PRBot.app to Applications, then open it to start the service.")
        return [str(path), "run"]
    return [sys.executable, "-m", "prbot.main", "run"]


def install():
    program = executable()
    stop()
    path = agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    spec = {
        "Label": LABEL,
        "ProgramArguments": program,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "WorkingDirectory": str(data_dir()),
        "EnvironmentVariables": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    }
    # Never inherit or copy the interactive shell's Token into launchd metadata.
    path.write_bytes(plistlib.dumps(spec))
    path.chmod(0o600)
    try:
        launchctl("bootstrap", domain(), str(path))
    except ConfigError:
        path.unlink(missing_ok=True)
        raise
    # Current login session only. Never leave a registration for the next login.
    path.unlink(missing_ok=True)


def stop():
    launchctl("bootout", f"{domain()}/{LABEL}", check=False)
    # Never delete registration if a running job could not be unloaded.
    if launchctl("print", f"{domain()}/{LABEL}", check=False).returncode == 0:
        raise ConfigError("Background service did not stop. Check current user permissions.")
    agent_path().unlink(missing_ok=True)


def report(state, detail):
    path = data_dir() / "status.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"state": state, "detail": detail, "time": int(time.time())}))
    tmp.chmod(0o600)
    tmp.replace(path)


def status():
    loaded = launchctl("print", f"{domain()}/{LABEL}", check=False).returncode == 0
    path = data_dir() / "status.json"
    try:
        last = json.loads(path.read_text())
    except (OSError, ValueError):
        last = {}
    from prbot.config import read_settings

    selected = read_settings().get("PRBOT_WORK_ROOT")
    workspace = (
        str(Path(selected) / "PRBot-workspaces") if selected else str(data_dir() / "workspaces")
    )
    from prbot.config import env_path

    return {
        "env_file": str(env_path()),
        "workspace_root": workspace,
        "agent_loaded": loaded,
        "last_service_report": last,
    }


def migrate_registration():
    """Remove old persistent registration without starting a service."""
    agent_path().unlink(missing_ok=True)
    from prbot.config import save_settings

    save_settings({})
