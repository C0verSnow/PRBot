import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from prbot.codex_adapter import CodexAdapter
from prbot.github import DeliveryError, GitHub


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


async def test_sdk_timeout_removes_private_credential_directory(tmp_path, monkeypatch):
    from pathlib import Path

    captured = []

    async def communicate(payload):
        private = Path(json.loads(payload)["private_home"])
        assert private.is_dir()
        (private / "auth.json").write_text("test credential")
        captured.append(private)
        raise TimeoutError

    process = Mock(pid=123456, returncode=None, communicate=communicate, wait=AsyncMock())
    monkeypatch.setattr(
        "prbot.codex_adapter.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
    )
    kill = Mock()
    monkeypatch.setattr("prbot.codex_adapter.os.killpg", kill)
    with pytest.raises(TimeoutError):
        await CodexAdapter("{}").invoke(tmp_path, "request", {}, 1)
    assert captured and not captured[0].exists()
    kill.assert_called_once()
