"""Read the user's Codex login; temporary edits live only in this process."""

import json
import threading
from pathlib import Path

from prbot.config import ConfigError

_lock = threading.Lock()
_override = None


def codex_login(id_token=None):
    global _override
    with _lock:
        try:
            auth = json.loads((Path.home() / ".codex/auth.json").read_text())
            tokens = auth["tokens"]
            if not isinstance(tokens.get("id_token"), str) or not tokens["id_token"].strip():
                raise ValueError
            if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
                raise ValueError
            if id_token is not None:
                if not isinstance(id_token, str) or not id_token.strip() or len(id_token) > 20000:
                    raise ValueError
                _override = id_token.strip()
            if _override:
                tokens["id_token"] = _override
            return json.dumps(auth)
        except (OSError, ValueError, KeyError, TypeError):
            raise ConfigError(
                "Codex sign-in not found or invalid. Sign in with Codex, "
                "then refresh. A complete auth.json with id_token and "
                "access_token is required."
            ) from None


def github_token(values):
    """Prefer Infisical, then ask the host Git credential provider without echoing it."""
    import subprocess

    if values.get("GITHUB_TOKEN"):
        return values["GITHUB_TOKEN"]
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            timeout=20,
            env={**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode or not fields.get("password"):
            raise ValueError
        return fields["password"]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ConfigError(
            "GitHub sign-in unavailable. Sign in with Git on this Mac or add "
            "GITHUB_TOKEN in Infisical, then retry."
        ) from None
