import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.error import Conflict, Forbidden, InvalidToken, NetworkError

from prbot.command_router import CommandRouter
from prbot.config import Config
from prbot.state import State, instance_lock
from prbot.telegram_client import Receiver, ServiceHalted, backoff, run


@pytest.fixture
def state():
    db = State("test-token")
    yield db
    db.close()


async def test_delivered_deduplicated_across_restart(update):
    state = State("test-token")
    bot = Mock(send_message=AsyncMock())
    await Receiver(bot, state, CommandRouter("MyBot")).handle(update())
    assert state.offset == 11
    state.close()
    reopened = State("test-token")
    try:
        await Receiver(bot, reopened, CommandRouter("MyBot")).handle(update())
        assert bot.send_message.await_count == 1
        assert reopened.record(10)[0] == "delivered"
    finally:
        reopened.close()


async def test_unconfirmed_reply_keeps_offset(update, state):
    bot = Mock(send_message=AsyncMock(side_effect=NetworkError("timeout")))
    receiver = Receiver(bot, state, CommandRouter("MyBot"))
    with pytest.raises(NetworkError):
        await receiver.handle(update())
    assert state.offset == 0
    assert state.record(10) == ("response_pending", 1)
    bot.send_message.side_effect = None
    await receiver.handle(update())
    assert state.offset == 11
    assert state.record(10) == ("delivered", 2)


async def test_retry_budget_halts(update, state):
    bot = Mock(send_message=AsyncMock(side_effect=NetworkError("timeout")))
    receiver = Receiver(bot, state, CommandRouter("MyBot"))
    for _ in range(5):
        with pytest.raises(NetworkError):
            await receiver.handle(update())
    with pytest.raises(ServiceHalted):
        await receiver.handle(update())
    assert state.offset == 0


async def test_forbidden_reply_is_failed_never_delivered(update, state):
    bot = Mock(send_message=AsyncMock(side_effect=Forbidden("blocked")))
    await Receiver(bot, state, CommandRouter("MyBot")).handle(update())
    assert state.record(10)[0] == "failed_permanent"
    assert state.offset == 11


async def test_plain_text_ignored(update, state):
    bot = Mock(send_message=AsyncMock())
    await Receiver(bot, state, CommandRouter("MyBot")).handle(update("hello"))
    bot.send_message.assert_not_awaited()
    assert state.offset == 11


async def test_handler_fault_does_not_acknowledge(update, state):
    router = CommandRouter("MyBot")
    router.register("broken", Mock(side_effect=ValueError("bad")))
    with pytest.raises(NetworkError):
        await Receiver(Mock(), state, router).handle(update("/broken"))
    assert state.offset == 0
    assert state.record(10) == ("failed", 1)


@pytest.mark.parametrize("error", [Conflict("conflict"), InvalidToken("invalid"), Forbidden("no")])
async def test_fatal_api_error_halts_without_retries(monkeypatch, state, error):
    bot = Mock(initialize=AsyncMock(side_effect=error), shutdown=AsyncMock())
    monkeypatch.setattr("prbot.telegram_client.Bot", lambda token: bot)
    with pytest.raises(ServiceHalted):
        await run(Config("test-token"), state, asyncio.Event(), Mock())
    bot.initialize.assert_awaited_once()
    bot.shutdown.assert_awaited_once()


async def test_network_recovers(monkeypatch, state):
    stop = asyncio.Event()
    bot = Mock(
        initialize=AsyncMock(side_effect=[NetworkError("offline"), None]),
        get_webhook_info=AsyncMock(return_value=Mock(url="")),
        username="MyBot",
        shutdown=AsyncMock(),
    )

    async def poll(**kwargs):
        stop.set()
        return []

    bot.get_updates = AsyncMock(side_effect=poll)
    sleep = AsyncMock()
    monkeypatch.setattr("prbot.telegram_client.Bot", lambda token: bot)
    monkeypatch.setattr("prbot.telegram_client.pause", sleep)
    await run(Config("test-token"), state, stop, Mock())
    assert bot.initialize.await_count == 2
    sleep.assert_awaited_once()


def test_token_progress_isolation(state):
    state.mark(10, "delivered", advance=True)
    other = State("different-token")
    try:
        assert other.offset == 0
    finally:
        other.close()


def test_single_instance():
    with instance_lock():
        with pytest.raises(RuntimeError):
            with instance_lock():
                pass


def test_backoff_is_bounded():
    assert 0 < backoff(1) <= 2
    assert 45 <= backoff(100) <= 60
