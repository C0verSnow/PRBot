import asyncio
import io
import json
import sys
from unittest.mock import AsyncMock, Mock

import pytest

from prbot.codex_adapter import CodexAdapter, SDKFailure, worker_failure
from prbot.config import Config
from prbot.telegram_client import ServiceHalted, run


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
    save = Mock()
    monkeypatch.setattr("prbot.task_config.credential", save)
    adapter = CodexAdapter("original-login")
    assert await adapter.invoke(tmp_path, "__prbot_account_check__", {}, 1) == {
        "authenticated": False,
    }
    save.assert_not_called()
    assert adapter.auth == "original-login"


async def test_service_retains_startup_category_without_raw_diagnostics(monkeypatch, caplog):
    from prbot.state import State
    from prbot.task_config import TaskConfig

    bot = Mock(
        initialize=AsyncMock(),
        get_webhook_info=AsyncMock(return_value=Mock(url="")),
        shutdown=AsyncMock(),
        username="test",
    )
    github = Mock(close=AsyncMock())
    monkeypatch.setattr("prbot.telegram_client.Bot", lambda token: bot)
    monkeypatch.setattr(TaskConfig, "load", lambda: TaskConfig(frozenset({1}), frozenset({"a/b"})))
    monkeypatch.setattr("prbot.execution_setup.execution_credentials", lambda: ("secret", "auth"))
    monkeypatch.setattr("prbot.github.GitHub", lambda token: github)
    monkeypatch.setattr(
        CodexAdapter,
        "check_auth",
        AsyncMock(side_effect=SDKFailure("initialize", "initialize", "RuntimeError")),
    )
    state = State("test")
    try:
        with pytest.raises(ServiceHalted, match="阶段=initialize"):
            await run(Config("test"), state, asyncio.Event(), Mock())
        assert "code=initialize stage=initialize type=RuntimeError" in caplog.text
        assert "secret" not in caplog.text
        github.close.assert_awaited_once()
        bot.shutdown.assert_awaited_once()
    finally:
        state.close()


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
