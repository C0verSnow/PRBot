import io
import json
import sys
from unittest.mock import AsyncMock, Mock

import pytest

from prbot.codex_adapter import CodexAdapter, SDKFailure, worker_failure


@pytest.mark.parametrize("stage", ["dependencies", "runtime", "auth", "initialize"])
def test_worker_error_preserves_safe_category(stage):
    failure = worker_failure(
        json.dumps(
            {
                "error": {
                    "stage": stage,
                    "type": "RuntimeError",
                    "kind": "failure",
                }
            }
        )
    )
    assert failure.code == stage
    assert failure.stage == stage
    assert failure.exception_type == "RuntimeError"


@pytest.mark.parametrize(
    "output",
    [
        "traceback secret-token",
        '{"error":{"stage":"secret-token","type":"RuntimeError","kind":"failure"}}',
        '{"error":{"stage":"account","type":"secret-token","kind":"failure"}}',
        "[]",
    ],
)
def test_untrusted_worker_error_never_echoed(output):
    failure = worker_failure(output)
    assert "secret-token" not in str(failure)
    assert failure.code == "failure"


async def test_startup_timeout_is_actionable(monkeypatch):
    monkeypatch.setattr(CodexAdapter, "invoke", AsyncMock(side_effect=TimeoutError))
    with pytest.raises(SDKFailure, match="timeout") as error:
        await CodexAdapter("{}").check_auth()
    assert error.value.stage == "startup"


async def test_missing_account_does_not_overwrite_saved_login(tmp_path, monkeypatch):
    process = Mock(
        pid=123456,
        returncode=0,
        wait=AsyncMock(),
        communicate=AsyncMock(
            return_value=(
                json.dumps({"result": {"authenticated": False}, "auth": "{}"}).encode(),
                b"",
            )
        ),
    )
    monkeypatch.setattr(
        "prbot.codex_adapter.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    )
    monkeypatch.setattr("prbot.codex_adapter.os.killpg", Mock())
    adapter = CodexAdapter("original-login")
    assert await adapter.invoke(tmp_path, "__prbot_account_check__", {}, 1) == {
        "authenticated": False,
    }
    assert adapter.auth == "original-login"


def test_worker_account_check_does_not_force_refresh(tmp_path, monkeypatch, capsys):
    from prbot import sdk_worker

    runtime = tmp_path / "codex"
    runtime.touch(mode=0o700)
    client = Mock()
    client.account.return_value = Mock(account=object())
    sdk = Mock()
    sdk.Codex.return_value.__enter__ = Mock(return_value=client)
    sdk.Codex.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setitem(sys.modules, "openai_codex", sdk)
    monkeypatch.setitem(sys.modules, "codex_cli_bin", Mock(bundled_codex_path=lambda: runtime))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "path": str(tmp_path),
                    "auth": "{}",
                    "prompt": "__prbot_account_check__",
                }
            )
        ),
    )
    sdk_worker.run()
    client.account.assert_called_once_with(refresh_token=False)
    assert json.loads(capsys.readouterr().out)["result"]["authenticated"]


def test_worker_exception_only_exposes_allowlisted_metadata(monkeypatch, capsys):
    from prbot import sdk_worker

    def fail():
        sdk_worker._stage = "account"
        raise RuntimeError("invalid_grant Bearer secret-token user@example.com")

    monkeypatch.setattr(sdk_worker, "_run", fail)
    with pytest.raises(SystemExit):
        sdk_worker.run()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "error": {
            "stage": "account",
            "type": "RuntimeError",
            "kind": "authentication",
        }
    }
    assert "secret-token" not in captured.out + captured.err
    assert "user@example.com" not in captured.out + captured.err


def test_real_sdk_transport_exception_type_survives_safe_boundary(monkeypatch, capsys):
    from openai_codex.errors import TransportClosedError

    from prbot import sdk_worker

    def fail():
        sdk_worker._stage = "initialize"
        raise TransportClosedError("connection closed: Bearer secret-token")

    monkeypatch.setattr(sdk_worker, "_run", fail)
    with pytest.raises(SystemExit):
        sdk_worker.run()
    captured = capsys.readouterr()
    failure = worker_failure(captured.out)
    assert failure.exception_type == "TransportClosedError"
    assert failure.code == "network"
    assert "secret-token" not in captured.out + str(failure)


async def test_real_full_access_runtime_initializes_without_credentials(tmp_path):
    result = await CodexAdapter("{}").invoke(tmp_path, "__prbot_runtime_check__", {}, 30)
    assert result == {"initialized": True, "thread_started": True}


def test_worker_explicitly_enables_host_tools_and_full_access(tmp_path, monkeypatch, capsys):
    from prbot import sdk_worker

    runtime = tmp_path / "codex"
    runtime.touch(mode=0o700)
    client = Mock()
    client.thread_start.return_value = Mock(id="thread")
    sdk = Mock()
    sdk.Codex.return_value.__enter__ = Mock(return_value=client)
    sdk.Codex.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setitem(sys.modules, "openai_codex", sdk)
    monkeypatch.setitem(sys.modules, "codex_cli_bin", Mock(bundled_codex_path=lambda: runtime))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "path": str(tmp_path),
                    "auth": "{}",
                    "prompt": "__prbot_runtime_check__",
                }
            )
        ),
    )
    sdk_worker.run()
    config = sdk.Codex.call_args.args[0]
    assert config.launch_args_override[0] == str(runtime)
    assert 'sandbox_mode="danger-full-access"' in config.launch_args_override
    assert 'approval_policy="never"' in config.launch_args_override
    assert not any("=false" in arg or "sandbox-exec" in arg for arg in config.launch_args_override)
    assert client.thread_start.call_args.kwargs["sandbox"] == sdk.Sandbox.full_access
    assert client.thread_start.call_args.kwargs["approval_mode"] == sdk.ApprovalMode.deny_all
