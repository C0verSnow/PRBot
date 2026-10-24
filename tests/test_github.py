import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from prbot.codex_adapter import CodexAdapter
from prbot.github import DeliveryError, GitHub


async def test_github_request_redacts_uncertain_network_error():
    def transport(request):
        raise httpx.ReadTimeout("private URL and credentials")

    gh = GitHub("test")
    await gh.client.aclose()
    gh.client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(transport)
    )
    try:
        with pytest.raises(DeliveryError, match="GitHub request unconfirmed") as error:
            await gh.request("GET", "/repos/o/r")
        assert "private" not in str(error.value)
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
