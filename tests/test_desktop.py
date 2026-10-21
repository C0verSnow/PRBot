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


@pytest.mark.parametrize(
    ("action", "automatic"),
    [("启动服务", False), ("重启服务", False), ("开启登录自启动", True), ("关闭登录自启动", False)],
)
def test_settings_service_actions_preserve_validation(monkeypatch, action, automatic):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from prbot import ui

    config = SimpleNamespace(token="private-token", autostart=False)
    monkeypatch.setattr(ui.Config, "load", lambda: config)
    validate = AsyncMock()
    install = Mock()
    monkeypatch.setattr(ui, "validate_api", validate)
    monkeypatch.setattr(ui.service, "install", install)
    monkeypatch.setattr(ui, "tell", Mock())
    ui.perform_action(action)
    validate.assert_awaited_once_with("private-token")
    install.assert_called_once_with(automatic)


def test_settings_token_uses_hidden_input_and_never_echoes_secret(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from prbot import ui

    token = "123456:" + "a" * 35
    dialog = Mock(return_value=token)
    stored = Mock()
    shown = Mock()
    monkeypatch.setattr(ui, "applescript", dialog)
    monkeypatch.setattr(ui, "validate_api", AsyncMock())
    monkeypatch.setattr(ui, "keychain_token", stored)
    monkeypatch.setattr(ui, "tell", shown)
    ui.perform_action("配置 Token")
    assert "with hidden answer" in dialog.call_args.args[0]
    assert token not in dialog.call_args.args[0]
    assert token not in shown.call_args.args[0]
    stored.assert_called_once_with(token)


def test_unknown_settings_action_cannot_execute(monkeypatch):
    from unittest.mock import Mock

    from prbot import ui

    install = Mock()
    monkeypatch.setattr(ui.service, "install", install)
    with pytest.raises(ConfigError):
        ui.perform_action("arbitrary-command")
    install.assert_not_called()
