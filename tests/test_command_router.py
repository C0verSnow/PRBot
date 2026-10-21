import pytest
from telegram import Update

from prbot.command_router import CommandRouter


@pytest.mark.parametrize("text", ["/id", "/id@MyBot", "/ID@mybot", "/id args"])
def test_id_forms(update, text):
    assert "User ID: 123" in CommandRouter("MyBot").dispatch(update(text))


@pytest.mark.parametrize("text", ["hello /id", "hello", "/id@OtherBot", "/id/", "/id!", "/"])
def test_ignores_non_commands(update, text):
    assert CommandRouter("MyBot").dispatch(update(text)) is None


def test_unknown_command(update):
    assert "仅支持 /id" in CommandRouter("MyBot").dispatch(update("/FinishIssue"))


def test_channel_and_empty_update(update):
    router = CommandRouter("MyBot")
    assert router.dispatch(update(chat_type="channel")) is None
    assert router.dispatch(Update(20)) is None


def test_registry():
    router = CommandRouter("MyBot")
    with pytest.raises(ValueError):
        router.register("id", lambda message: "duplicate")
