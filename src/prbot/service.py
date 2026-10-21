import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from prbot.config import ConfigError, save_settings
from prbot.paths import data_dir

LABEL = "org.prbot.agent"


def agent_path():
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def launchctl(*args, check=True):
    result = subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=20)
    if check and result.returncode:
        raise ConfigError("launchd 操作失败，请在已登录的 macOS 用户会话中操作并检查状态。")
    return result


def domain():
    return f"gui/{os.getuid()}"


def executable():
    if getattr(sys, "frozen", False):
        path = Path(sys.executable).resolve()
        if not str(path).startswith(
            ("/Applications/PRBot.app/", str(Path.home() / "Applications/PRBot.app") + "/")
        ):
            raise ConfigError("请先把 PRBot.app 拖到 Applications，再打开应用启动服务。")
        return [str(path), "run"]
    return [sys.executable, "-m", "prbot.main", "run"]


def install(autostart):
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
    save_settings({"PRBOT_AUTOSTART": str(autostart).lower()})
    if not autostart:
        # Loaded in the current session but not installed for subsequent logins.
        path.unlink()


def stop():
    launchctl("bootout", f"{domain()}/{LABEL}", check=False)
    # Never delete registration if a running job could not be unloaded.
    if launchctl("print", f"{domain()}/{LABEL}", check=False).returncode == 0:
        raise ConfigError("后台服务未能停止，请检查当前用户权限。")
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
    return {
        "agent_loaded": loaded,
        "autostart": agent_path().exists(),
        "last_service_report": last,
    }
