import pytest
from telegram import Update


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("prbot.config.env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr("prbot.secrets._cache", None)
    monkeypatch.setattr("prbot.execution_setup._override", None)
    for key in ("TELEGRAM_BOT_TOKEN", "PRBOT_LOG_LEVEL", "PRBOT_POLL_TIMEOUT", "PRBOT_AUTOSTART"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def update():
    def factory(text="/id", user_id=123, chat_id=123, chat_type="private", uid=10, **extra):
        message = {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
            **extra,
        }
        if user_id is not None:
            message["from"] = {"id": user_id, "is_bot": False, "first_name": "Test"}
        return Update.de_json({"update_id": uid, "message": message}, None)

    return factory
