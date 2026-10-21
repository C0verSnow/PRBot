"""Full Access SDK worker; temporary auth, host tools, explicit never-approve policy."""

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


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
        config = CodexConfig(
            launch_args_override=(
                runtime,
                "-c",
                'cli_auth_credentials_store="file"',
                "-c",
                'sandbox_mode="danger-full-access"',
                "-c",
                'approval_policy="never"',
                "app-server",
                "--listen",
                "stdio://",
            ),
            cwd=str(workspace),
            env={**os.environ, "CODEX_HOME": private},
        )
        _stage = "initialize"
        with Codex(config) as codex:
            if request["prompt"] == "__prbot_runtime_check__":
                thread = codex.thread_start(
                    cwd=str(workspace),
                    ephemeral=True,
                    sandbox=Sandbox.full_access,
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
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions="Follow applicable AGENTS.md. Use host tools as needed. "
                "Never run local builds or compilation tests. Return the supplied JSON schema.",
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
            "JsonRpcError",
            "TransportClosedError",
            "CodexRpcError",
            "ParseError",
            "InvalidRequestError",
            "MethodNotFoundError",
            "InvalidParamsError",
            "InternalRpcError",
            "ServerBusyError",
            "RetryLimitExceededError",
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
                "login required",
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
