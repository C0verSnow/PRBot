"""Issue #19 regression coverage. Run in remote CI, never local compilation."""

import asyncio
import json
import os
import signal
import sys
from unittest.mock import AsyncMock, Mock

import pytest

from prbot.codex_adapter import CodexAdapter
from prbot.config import ConfigError
from prbot.conversation_service import ConversationService, target_link


@pytest.fixture
def app():
    archive = Mock(save=AsyncMock(), messages=AsyncMock(return_value=[]))
    adapter = Mock(invoke=AsyncMock(return_value={
        "summary": "Done", "outcome": "completed", "pr_url": "", "validation": "Remote test"
    }))
    return ConversationService(archive=archive, adapter=adapter, values={})


async def submit(app, task_id="workspace-task", mode="GitHub", link="https://github.com/o/r"):
    return await app.submit(task_id, "local:me", "desktop", mode, link, "Change the workspace")


@pytest.mark.parametrize("suffix,kind,mode", [
    ("", "repository", "Issue"), ("/issues/19", "issues", "Issue"),
    ("/pull/20", "pull", "Pull Request"),
])
async def test_unified_routes_preserve_business_mode_and_sessions(app, suffix, kind, mode):
    first = await submit(app, "workspace-first", link="https://github.com/o/r" + suffix)
    second = await submit(app, "workspace-second", link="https://github.com/o/r" + suffix)
    assert first["mode"] == second["mode"] == mode
    assert json.loads(app.request(first["id"])["payload"])["kind"] == kind
    assert first["conversation_id"] != second["conversation_id"]
    assert await submit(app, "workspace-first", link="https://github.com/o/r" + suffix) == first


@pytest.mark.parametrize("link", [
    "https://github.com/o/r/actions", "https://github.com/o/r/pull/0",
    "http://github.com/o/r", "https://github.com.evil/o/r", "https://github.com/o/r#x",
    "https://github.com/o/r?x=y", "https://github.com/o/r/issues/no", "https://[broken",
])
def test_unified_rejects_unsupported_targets_with_clear_error(link):
    with pytest.raises(ConfigError):
        target_link(link, "GitHub")


async def test_cancel_queue_idempotency_ownership_and_no_later_execution(app):
    await submit(app)
    await submit(app, "unrelated-task")
    for actor, source in [("other", "desktop"), ("local:me", "telegram")]:
        with pytest.raises(ConfigError):
            app.cancel("workspace-task", actor, source)
    first = app.cancel("workspace-task", "local:me")
    assert first["status"] == "save_pending"
    assert app.cancel("workspace-task", "local:me") == first
    await app.save_result(app.request("workspace-task"))
    assert app.request("workspace-task")["status"] == "cancelled"
    assert app.request("unrelated-task")["status"] == "queued"
    app.adapter.invoke.assert_not_awaited()
    assert app.cancel("workspace-task", "local:me")["status"] == "cancelled"
    assert len(app.tasks("local:me")) == 2


async def test_running_cancel_waits_for_cleanup_and_survives_service_instance_change(app):
    await submit(app)
    started, cleanup_started, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release = asyncio.Event()

    async def invoke(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release.wait()
            cleaned.set()

    app.adapter.invoke.side_effect = invoke
    app.change("workspace-task", "running")
    execution = asyncio.create_task(app.execute_cancellable(app.request("workspace-task")))
    await asyncio.wait_for(started.wait(), 2)
    other = ConversationService(archive=app.archive, adapter=app.adapter, values={})
    assert other.cancel("workspace-task", "local:me")["status"] == "cancelling"
    await asyncio.wait_for(cleanup_started.wait(), 2)
    assert other.cancel("workspace-task", "local:me")["status"] == "cancelling"
    assert not cleaned.is_set()
    assert not execution.done()
    release.set()
    await asyncio.wait_for(execution, 2)
    assert cleaned.is_set()
    assert app.request("workspace-task")["status"] == "cancelled"
    saved = app.archive.save.call_args.args[0]
    assert saved["task_id"] == "workspace-task" and '"cancelled"' in saved["content"]


async def test_completion_wins_race_and_cancel_never_overwrites_it(app):
    await submit(app)
    app.change("workspace-task", "running")
    await app.execute_cancellable(app.request("workspace-task"))
    before = app.request("workspace-task")
    assert before["status"] == "completed"
    assert app.cancel("workspace-task", "local:me")["status"] == "completed"
    assert app.request("workspace-task") == before


async def test_cancel_wins_race_before_result_is_persisted(app):
    await submit(app)
    app.change("workspace-task", "running")

    async def racing_result(*args):
        app.cancel("workspace-task", "local:me")
        return {"summary": "Late result", "outcome": "completed", "validation": "", "pr_url": ""}

    app.adapter.invoke.side_effect = racing_result
    await app.execute_cancellable(app.request("workspace-task"))
    assert app.request("workspace-task")["status"] == "cancelled"
    assert "Late result" not in app.request("workspace-task")["result"]


async def test_cancel_archive_failure_retries_only_storage(app):
    await submit(app)
    app.cancel("workspace-task", "local:me")
    app.archive.save.side_effect = ConfigError("Offline")
    with pytest.raises(ConfigError):
        await app.save_result(app.request("workspace-task"))
    assert app.request("workspace-task")["status"] == "save_pending"
    app.archive.save.side_effect = None
    await app.retry_save("workspace-task", "local:me")
    assert app.request("workspace-task")["status"] == "cancelled"
    app.adapter.invoke.assert_not_awaited()


async def test_runner_claim_excludes_cancelled_queue_and_preserves_other_task(app):
    await submit(app)
    await submit(app, "unrelated-task")
    app.cancel("workspace-task", "local:me")
    stop = asyncio.Event()

    async def done(*args):
        stop.set()
        return {"summary": "Other", "outcome": "completed", "pr_url": "", "validation": ""}

    app.adapter.invoke.side_effect = done
    await asyncio.wait_for(app.run(stop, source="desktop"), 2)
    assert app.adapter.invoke.await_count == 1
    prompt = json.loads(app.adapter.invoke.call_args.args[1])
    assert prompt["continuation"]["task_id"] == "unrelated-task"
    assert app.request("workspace-task")["status"] == "save_pending"
    assert app.request("unrelated-task")["status"] == "completed"


async def test_sdk_cancel_kills_worker_and_inherited_child_process_group(tmp_path, monkeypatch):
    """Real OS processes, no real SDK/model calls; remote macOS runner only."""
    original = asyncio.create_subprocess_exec
    processes = []
    pidfile = tmp_path / "child.pid"
    worker = (
        "import subprocess,sys,time; "
        "from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"Path({str(pidfile)!r}).write_text(str(p.pid)); "
        "print('{\"type\":\"progress\",\"text\":\"ready\"}',flush=True); time.sleep(60)"
    )

    async def launch(*args, **kwargs):
        proc = await original(sys.executable, "-c", worker, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr("prbot.codex_adapter.asyncio.create_subprocess_exec", launch)
    started = asyncio.Event()

    async def event(data):
        started.set()

    execution = asyncio.create_task(CodexAdapter("{}", on_event=event).invoke(
        tmp_path, "test", {}, 10
    ))
    try:
        await asyncio.wait_for(started.wait(), 3)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, 3)
        assert processes[0].returncode == -signal.SIGKILL
        child = int(pidfile.read_text())
        # A reparented zombie may briefly remain visible, but cannot do any work.
        probe = await original("ps", "-o", "stat=", "-p", str(child),
                               stdout=asyncio.subprocess.PIPE)
        state, _ = await probe.communicate()
        assert not state.strip() or state.strip().startswith(b"Z")
    finally:
        for proc in processes:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)


async def test_old_grouped_history_and_new_session_history_restore_together(app):
    await submit(app)
    app.archive.messages.return_value = [
        {"conversation_id": app.conversation("local:me", "desktop", "Issue"),
         "message_id": "old-user", "task_id": "legacy-task", "mode": "Issue",
         "source": "desktop", "actor_id": "local:me", "role": "user",
         "content": "Legacy context", "sequence": 1},
        app.archive.save.call_args.args[0],
    ]
    page = await app.desktop_history("local:me", "All")
    assert {s["id"] for s in page["sessions"]} == {"legacy-task", "workspace-task"}
    selected = await app.desktop_history("local:me", "All", session="workspace-task")
    assert len(selected["messages"]) == 1
    assert selected["messages"][0]["task_id"] == "workspace-task"


async def test_native_stop_is_offline_and_cannot_take_telegram_identity(app, monkeypatch):
    from prbot.desktop import cancel_desktop_task

    await submit(app)
    loader = Mock(side_effect=AssertionError("Stop must not request cloud credentials"))
    monkeypatch.setattr("prbot.conversation_service.require_secrets", loader)
    assert cancel_desktop_task({"task_id": "workspace-task"}, "local:me")["status"] == (
        "save_pending"
    )
    with pytest.raises(ConfigError):
        cancel_desktop_task({"task_id": "workspace-task"}, "local:other")
    loader.assert_not_called()
