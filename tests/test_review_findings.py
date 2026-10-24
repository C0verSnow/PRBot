"""Issue #17: regressions run by remote CI only."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from prbot import journal, service, ui
from prbot.command_router import CommandRouter
from prbot.config import Config, ConfigError
from prbot.conversation_service import ConversationService
from prbot.conversation_store import ConversationStore
from prbot.desktop import run_desktop_tasks


async def test_desktop_executor_recovers_after_unexpected_failure(caplog):
    stop = asyncio.Event()
    runtime = {}

    async def succeed(stop, source):
        assert source == "desktop"
        assert runtime["executor"] == "running"
        stop.set()

    factory = Mock(side_effect=[
        OSError("private database content"),
        Mock(run=AsyncMock(side_effect=RuntimeError("private archive content"))),
        Mock(run=AsyncMock(side_effect=succeed)),
    ])
    await run_desktop_tasks(stop, runtime, factory, retry_delay=0.001)
    assert factory.call_count == 3
    assert "OSError" in caplog.text and "RuntimeError" in caplog.text
    assert "private" not in caplog.text


async def test_desktop_executor_cancellation_is_not_retried():
    factory = Mock(return_value=Mock(run=AsyncMock(side_effect=asyncio.CancelledError)))
    with pytest.raises(asyncio.CancelledError):
        await run_desktop_tasks(asyncio.Event(), {}, factory)
    factory.assert_called_once()


@pytest.mark.parametrize("text", ["hello", "/answer task-id reply", "/FinishIssue"])
def test_sync_conversation_dispatch_has_actionable_error(update, text):
    with pytest.raises(NotImplementedError, match="dispatch_async"):
        CommandRouter("testbot", Mock()).dispatch(update(text))


def test_config_default_matches_poll_validation():
    assert Config("test-token").poll_timeout == 10


def test_folder_selection_timeout_is_actionable(monkeypatch):
    import subprocess

    monkeypatch.setattr(ui.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("x", 45)))
    with pytest.raises(ConfigError, match="Folder selection timed out"):
        ui.perform_action("choose_workspace")


@pytest.mark.parametrize("result", ["legacy text", "null", "[]", "1", '"text"',
                                     '{"outcome":"save_pending"}', '{"outcome":null}'])
async def test_invalid_result_cannot_loop_in_save_pending(result):
    archive = Mock(save=AsyncMock())
    app = ConversationService(archive=archive, values={})
    await app.submit("request-one", "local:me", "desktop", "Issue", "https://github.com/o/r", "Fix")
    app.change("request-one", "save_pending", result)
    await app.save_result(app.request("request-one"))
    assert app.request("request-one")["status"] == "failed"
    assert json.loads(archive.save.await_args.args[0]["content"])["outcome"] == "failed"


async def test_history_builds_one_credential_snapshot_per_read(monkeypatch):
    auth = Mock(return_value=json.dumps({"tokens": {"id_token": "private-token"}}))
    monkeypatch.setattr("prbot.conversation_service.codex_login", auth)
    archive = Mock(messages=AsyncMock(return_value=[{
        "conversation_id": "conversation", "message_id": str(i), "task_id": "task",
        "source": "desktop", "actor_id": "local:me", "mode": "Issue",
        "role": "user", "content": "private-token", "sequence": i,
    } for i in range(100)]))
    app = ConversationService(archive=archive, values={})
    page = await app.desktop_history("local:me", "Issue", count=100)
    assert len(page["messages"]) == 100
    assert all(m["content"] == "[REDACTED]" for m in page["messages"])
    auth.assert_called_once()
    auth.return_value = json.dumps({"tokens": {"id_token": "replacement-token"}})
    archive.messages.return_value[0]["content"] = "replacement-token"
    page = await app.desktop_history("local:me", "Issue")
    assert page["messages"][0]["content"] == "[REDACTED]"
    assert auth.call_count == 2


def test_repeated_read_setup_has_no_ddl_or_scope_updates(monkeypatch):
    values = {"UPSTASH_REDIS_REST_URL": "https://history.upstash.io",
              "UPSTASH_REDIS_REST_TOKEN": "test-token"}
    ConversationService(archive=ConversationStore(values), values=values)
    schema = Mock(wraps=journal._create_schema)
    monkeypatch.setattr(journal, "_create_schema", schema)
    changed = []

    @contextmanager
    def tracked_db():
        with journal.journal_db() as db:
            yield db
            changed.append(db.total_changes)

    monkeypatch.setattr("prbot.conversation_store.journal_db", tracked_db)
    for _ in range(3):
        app = ConversationService(archive=ConversationStore(values), values=values)
        assert app.visible_tasks("local:me") == []
    schema.assert_not_called()
    assert changed == [0, 0, 0]


def test_concurrent_status_reports_use_separate_temporary_files():
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda i: service.report("running", str(i)), range(30)))
    path = service.data_dir() / "status.json"
    assert json.loads(path.read_text())["state"] == "running"
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob(".status-*.tmp"))


def test_missing_workspace_preserves_service_status(monkeypatch, tmp_path):
    monkeypatch.setenv("PRBOT_WORK_ROOT", str(tmp_path / "missing"))
    monkeypatch.setattr(service, "launchctl", Mock(return_value=Mock(returncode=0)))
    service.report("running", "Listening")
    state = service.status()
    assert state["workspace_root"] == str(tmp_path / "missing")
    assert "Choose a folder" in state["workspace_error"]
    assert state["agent_loaded"]
    assert state["last_service_report"]["state"] == "running"
