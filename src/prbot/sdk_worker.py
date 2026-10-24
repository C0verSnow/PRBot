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
_runtime_path = None
_workspace_path = None


def emit(event):
    sys.stdout.write(json.dumps({"v": 1, **event}, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def safe_text(text, request):
    from prbot.conversation_service import redact

    auth = json.loads(request["auth"])
    secrets = [
        value
        for key, value in os.environ.items()
        if any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "KEY"))
    ]
    secrets += list(auth.get("tokens", {}).values())
    for secret in secrets:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "[REDACTED]")
    return redact(text, secrets)[:8000]


def _run():
    global _stage, _runtime_path, _workspace_path
    from codex_cli_bin import bundled_codex_path
    from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

    _stage = "request"
    request = json.loads(sys.stdin.readline())
    workspace = Path(request["path"]).resolve()
    _workspace_path = str(workspace)
    _stage = "runtime"
    runtime = str(bundled_codex_path().resolve())
    _runtime_path = runtime
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
            try:
                task_prompt = json.loads(request["prompt"])
            except (ValueError, TypeError):
                task_prompt = {}
            private_chat = (
                isinstance(task_prompt, dict) and task_prompt.get("task", {}).get("kind") == "chat"
            )
            thread = codex.thread_start(
                cwd=str(workspace),
                ephemeral=True,
                sandbox=Sandbox.read_only if private_chat else Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions="Follow applicable AGENTS.md. Use host tools as needed. "
                "Never run local builds or compilation tests. Return the supplied JSON schema. "
                "If information or user confirmation is needed, stop safely and return "
                "outcome=needs_clarification with a clear question in summary. "
                "Do not use request_user_input. Never publish private chat to GitHub.",
            )
            turn = thread.turn(request["prompt"], output_schema=request["schema"])
            final_response = None
            questions = []
            completed = False
            for event in turn.stream():
                # Do not forward raw RPC payloads, commands, reasoning or tool output.
                if event.method == "item/started":
                    emit({"type": "progress", "text": "Codex is working on the task."})
                elif event.method == "item/completed":
                    item = event.payload.item
                    item = item.root if hasattr(item, "root") else item
                    if getattr(item, "type", None) == "agentMessage":
                        for question in getattr(item, "questions", None) or []:
                            title = question.title
                            if question.options:
                                title += "\nOptions: " + "; ".join(question.options)
                            questions.append(title)
                        phase = getattr(item, "phase", None)
                        phase = getattr(phase, "value", phase)
                        if phase in {None, "final_answer"}:
                            final_response = item.text
                        elif phase == "commentary":
                            emit({"type": "text", "text": safe_text(item.text, request)})
                elif event.method == "turn/completed":
                    completed = event.payload.turn.status.value == "completed"
            if not completed or (not final_response and not questions):
                raise RuntimeError("Codex turn did not complete")
            result = (
                {
                    "outcome": "needs_clarification",
                    "summary": "\n\n".join(questions),
                    "pr_url": "",
                    "validation": "Waiting for your answer",
                }
                if questions
                else json.loads(final_response)
            )
            if result.get("outcome") == "needs_clarification":
                emit({"type": "interaction", "text": safe_text(result.get("summary", ""), request)})
            emit({"type": "result", "result": result, "auth": auth.read_text()})


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
        metadata = {"stage": _stage, "type": name, "kind": kind}
        if isinstance(error, FileNotFoundError):
            metadata["file_target"] = (
                "unspecified"
                if error.filename is None
                else "runtime"
                if error.filename == _runtime_path
                else "workspace"
                if error.filename == _workspace_path
                else "other"
            )
            metadata["origin"] = "other"
            trace = error.__traceback__
            while trace:
                module = trace.tb_frame.f_globals.get("__name__")
                if module == "subprocess":
                    metadata["origin"] = "process_launch"
                elif module == "openai_codex.client":
                    metadata["origin"] = "sdk_client"
                trace = trace.tb_next
        sys.stdout.write(json.dumps({"error": metadata}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
