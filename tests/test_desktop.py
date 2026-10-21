"""Task handoff must not accept ambiguous URLs or bypass Telegram identity checks."""

import pytest

from prbot.config import ConfigError
from prbot.desktop import bot_url, prepare_draft


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        "",
        "x" * 3501,
        "https://github.com/owner/repo short",
        "https://github.com/owner/repo/issues/3 创建文件并更新说明",
        "https://github.com.evil/owner/repo 创建文件并更新说明",
        "https://user:pass@github.com/owner/repo 创建文件并更新说明",
    ],
)
def test_invalid_handoff(value):
    with pytest.raises(ConfigError):
        prepare_draft(value)


def test_handoff_preserves_requirement_without_command_or_fake_identity():
    assert prepare_draft(" https://github.com/Owner/Repo.git  创建文件\n并更新使用说明。 ") == (
        "https://github.com/owner/repo\n创建文件\n并更新使用说明。"
    )


@pytest.mark.parametrize("username", [None, "bad/name", "https://evil.example", "x\nuser"])
def test_bot_url_rejects_non_username(monkeypatch, username):
    from prbot import desktop

    class Config:
        token = "not-a-real-token"

        @classmethod
        def load(cls):
            return cls()

    async def validate(token):
        return username

    monkeypatch.setattr(desktop, "Config", Config)
    monkeypatch.setattr(desktop, "validate_api", validate)
    with pytest.raises(ConfigError):
        bot_url()


def test_bot_url_uses_configured_bot_not_user_supplied_target(monkeypatch):
    from prbot import desktop

    class Config:
        token = "not-a-real-token"

        @classmethod
        def load(cls):
            return cls()

    async def validate(token):
        assert token == "not-a-real-token"
        return "PRBot_test_bot"

    monkeypatch.setattr(desktop, "Config", Config)
    monkeypatch.setattr(desktop, "validate_api", validate)
    assert bot_url() == "https://t.me/PRBot_test_bot"
