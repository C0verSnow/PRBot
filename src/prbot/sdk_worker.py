"""Private subprocess entry; clean environment, isolated auth, no executable tools."""

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


def runtime_profile(workspace, auth_home, runtime):
    return "\n".join(
        [
            "(version 1)",
            "(deny default)",
            "(allow process-fork)",
            "(allow process-info* (target same-sandbox))",
            '(allow file-map-executable (subpath "/System") (subpath "/usr/lib") '
            f"(literal {json.dumps(runtime)}))",
            '(allow file-read* (literal "/") (subpath "/private/etc") '
            '(subpath "/private/var/db/timezone") (literal "/dev/random"))',
            '(allow file-read-data file-write-data (subpath "/dev/fd"))',
            '(allow system-mac-syscall (mac-policy-name "vnguard"))',
            '(allow system-mac-syscall (require-all (mac-policy-name "Sandbox") '
            "(mac-syscall-number 67)))",
            f"(allow process-exec (literal {json.dumps(runtime)}))",
            "(allow sysctl-read)",
            "(allow signal (target self))",
            "(allow mach-lookup)",
            '(allow ipc-posix-shm-read* (ipc-posix-name-prefix "apple.cfprefs."))',
            '(allow user-preference-read (preference-domain "com.openai.codex"))',
            '(allow file-read* (subpath "/Library/Managed Preferences") '
            '(subpath "/Library/Preferences"))',
            "(allow network*)",
            "(allow file-read-metadata)",
            '(allow file-read* (subpath "/System") (subpath "/usr/lib") '
            '(subpath "/Library/Apple") (literal "/dev/null") (literal "/dev/urandom"))',
            f"(allow file-read* (subpath {json.dumps(str(workspace))}) "
            f"(subpath {json.dumps(str(auth_home))}) (literal {json.dumps(runtime)}))",
            f'(allow file-write* (subpath {json.dumps(str(auth_home))}) (literal "/dev/null"))',
        ]
    )


@contextmanager
def private_home(request):
    if request.get("private_home"):
        # Created by the controller, which also cleans it after forced termination.
        path = Path(request["private_home"])
        if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
            raise RuntimeError("Invalid private runtime directory")
        yield str(path)
    else:
        with tempfile.TemporaryDirectory(prefix="prbot-sdk-") as directory:
            yield directory


_stage = "dependencies"


def _run():
    global _stage
    from codex_cli_bin import bundled_codex_path
    from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

    _stage = "request"
    request = json.load(sys.stdin)
    workspace = Path(request["path"]).resolve()
    _stage = "runtime"
    runtime = str(bundled_codex_path().resolve())
    if not os.access(runtime, os.X_OK):
        raise PermissionError("Runtime unavailable")
    with private_home(request) as private:
        auth_home = Path(private).resolve()
        os.chmod(private, 0o700)
        _stage = "auth"
        value = json.loads(request["auth"])
        if not isinstance(value, dict):
            raise ValueError("Invalid auth object")
        auth = auth_home / "auth.json"
        auth.write_text(request["auth"])
        auth.chmod(0o600)
        # No shell/rg/Python/tool subprocess may execute. Only app-server itself can start.
        profile = runtime_profile(workspace, auth_home, runtime)
        config = CodexConfig(
            launch_args_override=(
                "/usr/bin/sandbox-exec",
                "-p",
                profile,
                runtime,
                "-c",
                'cli_auth_credentials_store="file"',
                "-c",
                "features.shell_tool=false",
                "-c",
                "features.apps=false",
                "-c",
                "features.apply_patch_freeform=false",
                "-c",
                'web_search="disabled"',
                "app-server",
                "--listen",
                "stdio://",
            ),
            cwd=str(workspace),
            env={
                "HOME": private,
                "CODEX_HOME": private,
                "TMPDIR": private,
                "PATH": "/usr/bin:/bin",
            },
        )
        _stage = "initialize"
        with Codex(config) as codex:
            if request["prompt"] == "__prbot_runtime_check__":
                thread = codex.thread_start(
                    cwd=str(workspace),
                    ephemeral=True,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                )
                sys.stdout.write(
                    json.dumps(
                        {
                            "initialized": codex.metadata is not None,
                            "thread_started": bool(thread.id),
                        }
                    )
                )
                return
            if request["prompt"] == "__prbot_account_check__":
                _stage = "account"
                # Startup is a local account check, not a forced refresh/entitlement probe.
                authenticated = codex.account(refresh_token=False).account is not None
                sys.stdout.write(
                    json.dumps(
                        {"result": {"authenticated": authenticated}, "auth": auth.read_text()}
                    )
                )
                return
            _stage = "account"
            if codex.account().account is None:
                raise RuntimeError("Codex login required")
            _stage = "turn"
            thread = codex.thread_start(
                cwd=str(workspace),
                ephemeral=True,
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions="No tools. Use only supplied repository data. "
                "Return JSON matching the supplied schema.",
            )
            result = thread.run(request["prompt"], output_schema=request["schema"])
            if str(result.status.value) != "completed" or not result.final_response:
                raise RuntimeError("Codex turn did not complete")
            json.loads(result.final_response)
            sys.stdout.write(
                json.dumps({"result": json.loads(result.final_response), "auth": auth.read_text()})
            )


def run():
    try:
        _run()
    except Exception as error:
        # Raw SDK exceptions/stderr may contain tokens, user data or RPC payloads.
        # Emit only an allowlisted type and stage; no traceback crosses this boundary.
        name = type(error).__name__
        if name not in {
            "FileNotFoundError",
            "PermissionError",
            "TimeoutError",
            "RuntimeError",
            "ValueError",
            "JSONDecodeError",
            "ImportError",
            "ModuleNotFoundError",
            "CodexError",
            "CodexRequestError",
            "CodexTransportError",
            "OSError",
        }:
            name = "Exception"
        kind = "failure"
        message = str(error).lower()
        if any(
            value in message
            for value in (
                "refresh_token_reused",
                "refresh_token_expired",
                "invalid_grant",
                "unauthorized",
            )
        ):
            kind = "authentication"
        elif "timed out" in message or "timeout" in message:
            kind = "timeout"
        elif "connection" in message or "network" in message:
            kind = "network"
        sys.stdout.write(json.dumps({"error": {"stage": _stage, "type": name, "kind": kind}}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
