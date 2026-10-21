import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from telegram.error import NetworkError

from prbot.command_router import CommandRouter
from prbot.executor import Executor, send_outbox
from prbot.github import DeliveryError, GitHub
from prbot.state import State
from prbot.task_config import TaskConfig
from prbot.tasks import Conversations, Source, TaskStore, repository_url
from prbot.telegram_client import Receiver
from prbot.workspace import apply_edits, safe_path


@pytest.fixture
def store():
    state = State("tasks")
    yield TaskStore(state)
    state.close()


@pytest.fixture
def settings():
    return TaskConfig(frozenset({123}), frozenset({"owner/repo"}))


@pytest.fixture
def flow(store, settings):
    return Conversations(store, settings)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Owner/Repo",
        "https://github.com/owner/repo/",
        "https://github.com/owner/repo.git?q=yes#fragment",
    ],
)
def test_repository_normalization(url):
    assert repository_url(url) == "owner/repo"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r",
        "https://evil.test/o/r",
        "https://github.com/o/r/issues/3",
        "https://github.com/o/..",
        "https://user@github.com/o/r",
        "https://github.com:443/o/r",
        "https://github.com/o/r;echo",
        "https://github.com/o/%2e%2e",
    ],
)
def test_repository_rejects_invalid(url):
    with pytest.raises(ValueError):
        repository_url(url)


def test_conversation_and_replay(flow, store, update):
    assert "请输入 GitHub" in flow.handle(update("/FinishIssue"), "finishissue")
    assert "具体需求" in flow.handle(update("https://github.com/owner/repo", uid=11))
    msg = update("创建 test.md，内容为 test done", uid=12)
    reply = flow.handle(msg)
    assert "已受理" in reply
    assert flow.handle(msg) == reply
    task = store.list_status("queued")[0]
    assert task["source"] == Source().__dict__
    assert task["requirement"] == msg.message.text
    assert len(store.list_status("queued")) == 1


def test_session_isolation_cancel_and_expiry(flow, store, update):
    flow.handle(update("/FinishIssue"), "finishissue")
    assert flow.handle(update("https://github.com/owner/repo", chat_id=55, uid=11)) is None
    assert flow.handle(update("https://github.com/owner/repo", user_id=99, uid=12)) is None
    assert flow.handle(update("https://github.com/owner/repo", message_thread_id=7, uid=13)) is None
    with store.db:
        store.db.execute("UPDATE conversations SET expires=?", (time.time() - 1,))
    assert "过期" in flow.handle(update("https://github.com/owner/repo", uid=14))
    flow.handle(update("/FinishIssue", uid=15), "finishissue")
    assert "已取消" in flow.handle(update("/cancel", uid=16), "cancel")
    assert flow.handle(update("https://github.com/owner/repo", uid=17)) is None


def test_command_targets_and_other_commands(flow, update):
    router = CommandRouter("MyBot", flow)
    assert router.dispatch(update("/FinishIssue@OtherBot")) is None
    assert "请输入" in router.dispatch(update("/FINISHISSUE@MyBot"))
    assert "User ID" in router.dispatch(update("/id", uid=11))
    assert flow.store.session("123:123:0")[0] == "repository"
    assert "已取消" in router.dispatch(update("/CANCEL@MyBot", uid=12))


def test_requirement_and_allowlist(flow, store, update):
    flow.handle(update("/FinishIssue"), "finishissue")
    assert "有效" in flow.handle(update("not-a-repository", uid=11))
    assert "范围" in flow.handle(update("https://github.com/o/r", uid=12))
    flow.handle(update("https://github.com/owner/repo", uid=13))
    assert "不够具体" in flow.handle(update("改好", uid=14))
    assert store.list_status("queued") == []


def test_active_dedup_and_cancel(flow, store, update):
    scope = "123:123:0"
    with store.db:
        first = store.submit(10, scope, "owner/repo", "create a test file", update().message)
        second = store.submit(11, scope, "owner/repo", "create a test file", update().message)
    assert first["id"] == second["id"]
    assert "不适用" in flow.handle(update("/cancel", uid=12), "cancel")
    store.change(first["id"], "failed")
    with store.db:
        third = store.submit(12, scope, "owner/repo", "create a test file", update().message)
    assert third["id"] != first["id"]


async def test_reply_failure_does_not_repeat_conversation(flow, store, update):
    bot = Mock(send_message=AsyncMock(side_effect=NetworkError("timeout")))
    state = SimpleNamespace(
        db=store.db, offset=0, record=lambda uid: None, mark=lambda *a, **kw: None
    )
    receiver = Receiver(bot, state, CommandRouter("MyBot", flow))
    with pytest.raises(NetworkError):
        await receiver.handle(update("/FinishIssue"))
    expires = store.session("123:123:0")[2]
    bot.send_message.side_effect = None
    await receiver.handle(update("/FinishIssue"))
    assert store.session("123:123:0")[2] == expires


@pytest.mark.parametrize("name", ["../escape", "/tmp/outside", ".env", ".git/config", "auth.json"])
def test_edits_confined(tmp_path, name):
    with pytest.raises(DeliveryError):
        safe_path(tmp_path, name)


def test_symlink_escape(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(DeliveryError):
        safe_path(tmp_path, "link/escape")


def test_apply_edits_and_delete(tmp_path):
    apply_edits(tmp_path, {"edits": [{"path": "test.md", "content": "test done"}]})
    assert (tmp_path / "test.md").read_text() == "test done"
    apply_edits(tmp_path, {"edits": [{"path": "test.md", "content": None}]})
    assert not (tmp_path / "test.md").exists()


@pytest.fixture
def task(store, update):
    with store.db:
        return store.submit(
            10, "123:123:0", "owner/repo", "create test.md with test done", update().message
        )


async def test_sdk_failure_and_timeout(store, settings, task, monkeypatch, tmp_path):
    monkeypatch.setattr("prbot.executor.prepare", AsyncMock(return_value=(tmp_path, "base")))
    gh = Mock(repository=AsyncMock(return_value={"default_branch": "main"}))
    sdk = Mock(auth="secret", execute=AsyncMock(side_effect=TimeoutError()))
    executor = Executor(store, settings, gh, sdk, "token")
    await executor.execute(task)
    assert store.get(task["id"])["status"] == "failed"
    assert "超时" in store.get(task["id"])["error"]
    gh.pull_request.assert_not_called()


async def test_restart_marks_interrupted(store, settings, task):
    store.change(task["id"], "running")
    sdk = Mock()
    executor = Executor(store, settings, Mock(), sdk, "token")
    await executor.recover()
    assert store.get(task["id"])["status"] == "interrupted"
    sdk.execute.assert_not_called()


async def test_worker_concurrency_and_stop(store, settings, task):
    stop = asyncio.Event()
    executor = Executor(store, settings, Mock(), Mock(), "token")
    entered = asyncio.Event()

    async def execute(t):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            store.change(t["id"], "interrupted")

    executor.execute = execute
    running = asyncio.create_task(executor.run(stop))
    await asyncio.wait_for(entered.wait(), 2)
    assert len(executor.active) == 1
    stop.set()
    await asyncio.wait_for(running, 2)
    assert store.get(task["id"])["status"] == "interrupted"


async def test_outbox_separate_from_execution(store, task):
    store.change(task["id"], "succeeded", summary="done")
    store.notify(store.get(task["id"]))
    stop = asyncio.Event()

    async def send(**kwargs):
        stop.set()
        raise NetworkError("timeout")

    await send_outbox(store, Mock(send_message=send), stop)
    assert store.get(task["id"])["status"] == "succeeded"
    assert store.db.execute("SELECT status,attempts FROM task_outbox").fetchone() == ("pending", 1)


async def test_github_uncertain_pr_reconciled():
    calls = []

    def transport(request):
        calls.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("uncertain")
        prs = (
            []
            if len(calls) == 1
            else [{"state": "open", "html_url": "https://github.com/o/r/pull/1"}]
        )
        return httpx.Response(200, json=prs)

    gh = GitHub("test")
    await gh.client.aclose()
    gh.client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(transport)
    )
    try:
        result = await gh.pull_request(
            {
                "repo": "o/r",
                "branch": "feature/task",
                "id": "1",
                "repository": {"default_branch": "main"},
            },
            "body",
        )
        assert result["html_url"].endswith("/1")
        assert calls == ["GET", "POST", "GET"]
    finally:
        await gh.close()


async def test_issue_close_failure_checked():
    gh = GitHub("test")
    gh.request = AsyncMock(side_effect=[{"state": "open"}, DeliveryError("no"), {"state": "open"}])
    try:
        with pytest.raises(DeliveryError):
            await gh.close_issue("o/r", 3)
    finally:
        await gh.close()


async def test_required_checks_fail_closed():
    gh = GitHub("test")
    gh.request = AsyncMock(
        side_effect=[{"check_runs": []}, {"statuses": []}, {"workflow_runs": []}]
    )
    try:
        assert not await gh.checks("o/r", "sha", ("test", "dmg"))
    finally:
        await gh.close()


def test_credentials_not_saved():
    from prbot.config import save_settings
    from prbot.paths import data_dir

    save_settings({"GITHUB_TOKEN": "private", "OPENAI_API_KEY": "private"})
    assert json.loads((data_dir() / "config.json").read_text()) == {}


async def test_permission_failure_before_acceptance(flow, store, update):
    flow.github = Mock(repository=AsyncMock(side_effect=DeliveryError("仓库无权限")))
    flow.handle(update("/FinishIssue"), "finishissue")
    flow.handle(update("https://github.com/owner/repo", uid=11))
    reply = await flow.handle_async(update("创建 test.md 内容 test done", uid=12))
    assert "无权限" in reply
    assert store.list_status("queued") == []


async def test_delivery_requires_remote_checks(store, settings, task, monkeypatch, tmp_path):
    monkeypatch.setattr("prbot.executor.prepare", AsyncMock(return_value=(tmp_path, "base")))
    monkeypatch.setattr("prbot.executor.git", AsyncMock(return_value="commit"))
    monkeypatch.setattr(
        "prbot.executor.inspect_diff", AsyncMock(return_value=("diff", ["test.md", "README.md"]))
    )
    gh = Mock(
        repository=AsyncMock(return_value={"default_branch": "main"}),
        pull_request=AsyncMock(
            return_value={"html_url": "https://github.com/owner/repo/pull/1", "number": 1}
        ),
        wait_checks=AsyncMock(),
        request=AsyncMock(),
        ready=AsyncMock(),
    )
    sdk = Mock(
        auth="private",
        execute=AsyncMock(
            return_value={
                "summary": "created test",
                "clarification": "",
                "edits": [{"path": "test.md", "content": "test done"}],
            }
        ),
        review=AsyncMock(return_value={"passed": True, "reason": "verified"}),
    )
    executor = Executor(store, settings, gh, sdk, "token")

    async def checks(*args):
        assert store.get(task["id"])["status"] == "validating"
        gh.ready.assert_not_awaited()

    gh.wait_checks.side_effect = checks
    await executor.execute(task)
    assert store.get(task["id"])["status"] == "succeeded"
    gh.ready.assert_awaited_once()
    sdk.execute.assert_awaited_once()


async def test_sdk_protocol_initializes_without_credentials(tmp_path):
    # Real pinned runtime, no paid turn and no GitHub write; only remote CI runs tests.
    import os

    from codex_cli_bin import bundled_codex_path
    from openai_codex import ApprovalMode, Codex, CodexConfig

    assert bundled_codex_path().is_file()
    assert ApprovalMode.deny_all.value == "deny_all"
    with Codex(
        CodexConfig(env={"CODEX_HOME": str(tmp_path), "HOME": str(tmp_path), "OPENAI_API_KEY": ""})
    ) as sdk:
        assert sdk.metadata is not None
        assert sdk.account().account is None
        assert "GITHUB_TOKEN" not in os.environ


async def test_sandboxed_sdk_runtime_initializes(tmp_path):
    from prbot.codex_adapter import CodexAdapter

    result = await CodexAdapter("{}").invoke(tmp_path, "__prbot_runtime_check__", {}, 30)
    assert result == {"initialized": True, "thread_started": True}


async def test_sandbox_restricts_host_reads_and_execution(tmp_path):
    from pathlib import Path

    from prbot.sdk_worker import runtime_profile

    work = tmp_path / "work"
    private = tmp_path / "private"
    work.mkdir()
    private.mkdir()
    (work / "allowed.txt").write_text("allowed")
    (tmp_path / "unrelated-credential").write_text("private")
    profile = runtime_profile(work.resolve(), private.resolve(), "/bin/cat")

    async def command(*args):
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec",
            "-p",
            profile,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        return proc.returncode, out

    assert await command("/bin/cat", str(work / "allowed.txt")) == (0, b"allowed")
    assert (await command("/bin/cat", str(tmp_path / "unrelated-credential")))[0] != 0
    assert (await command("/bin/sh", "-c", "echo escape"))[0] != 0
    assert Path("/usr/bin/sandbox-exec").exists()


def test_selected_work_root_persists_and_is_reported(tmp_path):
    from prbot import service
    from prbot.task_config import select_work_root

    selected = tmp_path / "selected"
    selected.mkdir()
    assert select_work_root(str(selected)) == str(selected.resolve())
    assert TaskConfig.load().workspace_root == str(selected.resolve())
    assert service.status()["workspace_root"] == str(selected / "PRBot-workspaces")


def test_invalid_work_root_preserves_previous_choice(tmp_path):
    from prbot.config import ConfigError
    from prbot.task_config import select_work_root

    select_work_root(str(tmp_path))
    with pytest.raises(ConfigError):
        select_work_root(str(tmp_path / "missing"))
    assert TaskConfig.load().workspace_root == str(tmp_path.resolve())


async def test_new_tasks_use_selected_parent_without_touching_existing_files(
    tmp_path, settings, task, monkeypatch
):
    from dataclasses import replace

    from prbot.workspace import prepare

    selected = tmp_path / "chosen"
    selected.mkdir()
    original_mode = selected.stat().st_mode
    existing = selected / "my-file"
    existing.write_text("preserve")
    configured = replace(settings, workspace_root=str(selected))
    monkeypatch.setattr("prbot.workspace.git", AsyncMock(return_value="base"))
    task["repository"] = {"default_branch": "main"}
    path, sha = await prepare(task, configured, "token")
    assert path == selected / "PRBot-workspaces" / task["id"]
    assert sha == "base"
    assert existing.read_text() == "preserve"
    assert selected.stat().st_mode == original_mode
    assert (selected / "PRBot-workspaces").stat().st_mode & 0o777 == 0o700


async def test_independent_review_receives_associated_issue_context(tmp_path):
    from prbot.codex_adapter import CodexAdapter

    adapter = CodexAdapter("{}")
    adapter.invoke = AsyncMock(return_value={"passed": True, "reason": "covered"})
    context = {"number": 5, "body": "Create test_issue.md containing issue done"}
    await adapter.review(
        tmp_path, {"requirement": "Complete associated issue", "context": context}, "diff", 30
    )
    prompt = json.loads(adapter.invoke.call_args.args[1])
    assert prompt["associated_context"] == context


def test_workspace_parent_rules_are_supplied_in_order(tmp_path):
    from prbot.codex_adapter import parent_rules

    parent = tmp_path / "selected"
    child = parent / "PRBot-workspaces" / "task"
    child.mkdir(parents=True)
    (parent / "AGENTS.md").write_text("No local compilation. Use four-quadrant tasklist.")
    rules = parent_rules(child)
    assert rules[-1]["instructions"] == "No local compilation. Use four-quadrant tasklist."
    assert rules[-1]["path"] == str(parent / "AGENTS.md")


@pytest.mark.parametrize("conclusion", ["cancelled", "failure", "success"])
async def test_pending_successor_workflow_prevents_early_delivery_or_failure(conclusion):
    gh = GitHub("test")
    gh.request = AsyncMock(
        side_effect=[
            {"check_runs": [{"name": "test", "status": "completed", "conclusion": conclusion}]},
            {"statuses": []},
            {"workflow_runs": [{"status": "queued"}]},
        ]
    )
    try:
        assert not await gh.checks("o/r", "sha", ("test",))
    finally:
        await gh.close()


async def test_terminal_check_failure_after_all_runs_finish():
    gh = GitHub("test")
    gh.request = AsyncMock(
        side_effect=[
            {"check_runs": [{"name": "test", "status": "completed", "conclusion": "failure"}]},
            {"statuses": []},
            {"workflow_runs": [{"status": "completed"}]},
        ]
    )
    try:
        with pytest.raises(DeliveryError):
            await gh.checks("o/r", "sha", ("test",))
    finally:
        await gh.close()


async def test_task_records_checked_independently_and_utf8_names_preserved(tmp_path, monkeypatch):
    from prbot.workspace import inspect_diff

    async def fake_git(path, *args, **kwargs):
        if "--name-only" in args:
            assert "-z" in args
            return "test.md\0README.md\0tasklist.md\0使用手册.md\0"
        return "diff" if "--no-ext-diff" in args else ""

    monkeypatch.setattr("prbot.workspace.git", fake_git)
    (tmp_path / "test.md").write_text("test done")
    (tmp_path / "tasklist.md").write_text("# Tasklist\n- created file without timestamp")
    task = {
        "requirement": "create test.md with test done",
        "parent_rules": [{"instructions": "tasklist 为4象限，每一条记录带 Unix Timestamp"}],
    }
    with pytest.raises(DeliveryError, match="四象限"):
        await inspect_diff(tmp_path, task)
    (tmp_path / "tasklist.md").write_text(
        "# 本文档为4象限的任务清单，全文书写大白话\n"
        "## 想做：\n## 做完：\n- [1792550204] 已创建测试文件。\n## 没做：\n## 在做：\n"
    )
    _, names = await inspect_diff(tmp_path, task)
    assert "使用手册.md" in names
