"""Read the user's Codex login; temporary edits live only in this process."""

import base64
import json
import re
import threading
from pathlib import Path

from prbot.config import ConfigError

_lock = threading.Lock()
_override = None
_session_auth = None


def fetch_codex_id_token():
    """Read only the native ID token, ignoring PRBot's in-memory authentication edits.

    This is structural validation, not signature or account verification. Never
    invoke the SDK, refresh credentials, or persist the returned value here.
    """
    path = Path.home() / ".codex/auth.json"
    try:
        content = path.read_text()
    except FileNotFoundError:
        raise ConfigError(
            "Codex credentials file is missing. Sign in with Codex on this Mac, then retry."
        ) from None
    except (OSError, UnicodeError):
        raise ConfigError(
            "Cannot read Codex credentials. Check auth.json read permissions and retry."
        ) from None
    try:
        auth = json.loads(content)
        tokens = auth.get("tokens") if isinstance(auth, dict) else None
        token = tokens.get("id_token") if isinstance(tokens, dict) else None
        if not isinstance(token, str) or not token.strip() or len(token) > 20000:
            raise ValueError
        token = token.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
            raise ValueError
        header, payload, _signature = token.split(".")
        header = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
        payload = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if (
            not isinstance(header, dict)
            or header.get("alg") not in {"RS256", "ES256"}
            or not isinstance(payload, dict)
            or not isinstance(payload.get("sub"), str)
            or not payload["sub"]
        ):
            raise ValueError
        return token
    except (ValueError, TypeError, UnicodeError):
        raise ConfigError(
            "Codex credentials do not contain a valid tokens.id_token. "
            "An access token or API key cannot replace an ID token. Check your Codex sign-in."
        ) from None


def codex_login(id_token=None):
    global _override, _session_auth
    with _lock:
        try:
            original = (Path.home() / ".codex/auth.json").read_text()
            auth = json.loads(original)
            if _session_auth and _session_auth[0] == original:
                auth = json.loads(_session_auth[1])
            if not isinstance(auth, dict) or not isinstance(auth.get("tokens"), dict):
                raise ValueError
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


def retain_refreshed_login(original, updated):
    """Keep SDK refreshes across tasks; atomically update an unchanged native sign-in.

    Manual ID edits never overwrite auth.json. A concurrent Codex login wins.
    """
    global _session_auth
    import os
    import tempfile

    with _lock:
        path = Path.home() / ".codex/auth.json"
        try:
            current = path.read_text()
            native = json.loads(current)
            original_value = json.loads(original)
            updated_value = json.loads(updated)
            if not updated_value.get("tokens", {}).get("access_token"):
                return
            if _override:
                _session_auth = (current, updated)
                return
            if native != original_value:
                return
            fd, temporary = tempfile.mkstemp(prefix=".prbot-auth-", dir=path.parent)
            try:
                with os.fdopen(fd, "w") as output:
                    output.write(updated)
                    output.flush()
                    os.fsync(output.fileno())
                # Preserve a newer login written while the SDK was running.
                if path.read_text() == current:
                    os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            raise ConfigError(
                "Codex sign-in refresh could not be retained. Sign in again."
            ) from None
