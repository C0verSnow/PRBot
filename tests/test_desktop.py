from unittest.mock import AsyncMock, Mock

import pytest

from prbot import ui
from prbot.config import Config, ConfigError


def test_service_start_validates_infisical_token_first(monkeypatch):
    monkeypatch.setattr(Config, "load", lambda: Config("private-token"))
    validate = AsyncMock()
    install = Mock()
    monkeypatch.setattr(ui, "validate_api", validate)
    monkeypatch.setattr(ui.service, "install", install)
    ui.perform_action("start_service")
    validate.assert_awaited_once_with("private-token")
    install.assert_called_once_with()


def test_cancelled_workspace_selection_preserves_configuration(monkeypatch):
    selected = Mock()
    monkeypatch.setattr(ui, "applescript", lambda source: None)
    monkeypatch.setattr("prbot.task_config.select_work_root", selected)
    assert "cancelled" in ui.perform_action("choose_workspace")
    selected.assert_not_called()


def test_unknown_action_cannot_execute(monkeypatch):
    install = Mock()
    monkeypatch.setattr(ui.service, "install", install)
    with pytest.raises(ConfigError):
        ui.perform_action("arbitrary-command")
    install.assert_not_called()
