"""Issue #21 model contract, run by the existing remote macOS CI."""

import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from prbot.codex_adapter import CodexAdapter, SDKFailure, worker_failure
from prbot.config import ConfigError
from prbot.conversation_service import ConversationService
from prbot.sdk_worker import catalog


@pytest.fixture
def app():
    adapter = Mock(
        list_models=AsyncMock(return_value={"models": [{"id": "fixture-codex"}]}),
        invoke=AsyncMock(
            return_value={
                "summary": "Which branch?",
                "outcome": "needs_clarification",
                "validation": "Waiting",
                "pr_url": "",
            }
        ),
    )
    return ConversationService(archive=Mock(save=AsyncMock()), adapter=adapter, values={})


async def submit(app, request="model-request-001", model=None):
    return await app.submit(
        request, "local:test", "desktop", "Chat History", "", "Help me", model=model
    )


async def test_selected_model_survives_acceptance_retry_restart_and_answer(app):
    accepted = await submit(app, model="fixture-codex")
    assert accepted["model"] == "fixture-codex"
    # An overlapping retry cannot change an already accepted task's model.
    assert (await submit(app, model="another-model"))["model"] == "fixture-codex"
    reopened = ConversationService(archive=app.archive, adapter=app.adapter, values={})
    await reopened.execute(reopened.request(accepted["id"]))
    assert app.adapter.invoke.call_args.kwargs == {"model": "fixture-codex"}
    await reopened.answer(accepted["id"], "answer-001", "local:test", "desktop", "Use main")
    await reopened.execute(reopened.request(accepted["id"]))
    assert app.adapter.invoke.call_args.kwargs == {"model": "fixture-codex"}
    assert json.loads(app.adapter.invoke.call_args.args[1])["task"]["model"] == "fixture-codex"


async def test_failed_archive_retry_keeps_original_model(app):
    app.archive.save.side_effect = ConfigError("Offline")
    with pytest.raises(ConfigError, match="Offline"):
        await submit(app, model="fixture-codex")
    app.archive.save.side_effect = None
    assert (await submit(app, model="changed-model"))["model"] == "fixture-codex"


@pytest.mark.parametrize("model", ["unknown-model", "", "bad model", {"id": "fixture-codex"}])
async def test_invalid_or_unlisted_model_never_accepted(app, model):
    with pytest.raises(ConfigError, match="model"):
        await submit(app, model=model)
    assert app.request("model-request-001") is None
    app.archive.save.assert_not_awaited()


async def test_default_and_legacy_payloads_do_not_override_sdk_model(app):
    accepted = await submit(app)
    app.adapter.list_models.assert_not_awaited()
    with app.db() as db:
        row = app.request(accepted["id"])
        payload = json.loads(row["payload"])
        payload.pop("model")
        db.execute(
            "UPDATE requests SET payload=? WHERE id=?", (json.dumps(payload), accepted["id"])
        )
    await app.execute(app.request(accepted["id"]))
    assert app.adapter.invoke.call_args.kwargs == {}
    assert app.public(app.request(accepted["id"]))["model"] is None


def test_catalog_filters_hidden_non_text_duplicates_and_uses_model_slug():
    def option(slug, **extra):
        return SimpleNamespace(
            model=slug,
            display_name=slug,
            hidden=False,
            input_modalities=["text"],
            is_default=False,
            **extra,
        )

    hidden = option("hidden")
    hidden.hidden = True
    image = option("image-only")
    image.input_modalities = ["image"]
    client = Mock(
        models=Mock(
            return_value=SimpleNamespace(
                data=[hidden, option("visible"), image, option("visible")], next_cursor=None
            )
        )
    )
    assert catalog(client) == [{"id": "visible", "name": "visible", "default": False}]
    client.models.assert_called_once_with(include_hidden=False)
    client.models.return_value.next_cursor = "next-page"
    with pytest.raises(RuntimeError, match="incomplete"):
        catalog(client)


async def test_controller_passes_model_to_worker_without_fallback(tmp_path, monkeypatch):
    proc = Mock(
        pid=123456,
        returncode=0,
        wait=AsyncMock(),
        communicate=AsyncMock(return_value=(b'{"result":{"summary":"ok"},"auth":"{}"}', b"")),
    )
    monkeypatch.setattr(
        "prbot.codex_adapter.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
    )
    monkeypatch.setattr("prbot.codex_adapter.os.killpg", Mock())
    await CodexAdapter("{}").invoke(tmp_path, "task", {}, 2, model="fixture-codex")
    assert json.loads(proc.communicate.call_args.args[0])["model"] == "fixture-codex"


@pytest.mark.parametrize("scenario", ["success", "unavailable", "rerouted", "failed"])
def test_worker_uses_selected_model_on_real_thread_start_call(
    tmp_path, monkeypatch, capsys, scenario
):
    from prbot import sdk_worker

    runtime = tmp_path / "codex"
    runtime.touch(mode=0o700)
    client = Mock()
    client.models.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(
                model="fixture-codex",
                display_name="Fixture",
                hidden=False,
                input_modalities=["text"],
                is_default=True,
            )
        ],
        next_cursor=None,
    )
    client.thread_start.return_value.turn.return_value.stream.return_value = [
        SimpleNamespace(
            method="item/completed",
            payload=SimpleNamespace(
                item=SimpleNamespace(
                    type="agentMessage", text='{"summary":"ok"}', questions=[], phase="final_answer"
                )
            ),
        ),
        SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(
                turn=SimpleNamespace(status=SimpleNamespace(value="completed"))
            ),
        ),
    ]
    if scenario == "unavailable":
        client.models.return_value.data = []
    elif scenario == "rerouted":
        client.thread_start.return_value.turn.return_value.stream.return_value = [
            SimpleNamespace(method="model/rerouted")
        ]
    elif scenario == "failed":
        client.thread_start.return_value.turn.return_value.stream.return_value = [
            SimpleNamespace(
                method="turn/completed",
                payload=SimpleNamespace(
                    turn=SimpleNamespace(status=SimpleNamespace(value="failed"))
                ),
            )
        ]
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
                    "prompt": "task",
                    "schema": {},
                    "model": "fixture-codex",
                }
            )
        ),
    )
    if scenario == "success":
        sdk_worker.run()
        assert client.thread_start.call_args.kwargs["model"] == "fixture-codex"
        assert json.loads(capsys.readouterr().out.splitlines()[-1])["result"]["summary"] == "ok"
    else:
        with pytest.raises(SystemExit):
            sdk_worker.run()
        output = json.loads(capsys.readouterr().out.splitlines()[-1])
        assert output["error"]["kind"] == "model"
        assert client.thread_start.call_count == (0 if scenario == "unavailable" else 1)


def test_pinned_sdk_exposes_supported_model_configuration():
    import inspect

    from openai_codex import Codex

    assert "model" in inspect.signature(Codex.thread_start).parameters
    assert "include_hidden" in inspect.signature(Codex.models).parameters


def test_model_error_is_safe_and_actionable():
    failure = worker_failure(
        json.dumps(
            {
                "error": {
                    "stage": "turn",
                    "type": "CodexRpcError",
                    "kind": "model",
                }
            }
        )
    )
    assert isinstance(failure, SDKFailure)
    assert failure.code == "model"
    assert "No fallback" in str(failure)
