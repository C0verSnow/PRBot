import logging
from unittest.mock import Mock

import pytest

from prbot import service
from prbot.config import ConfigError
from prbot.logging_config import Redactor


def test_no_autostart_removes_plist_after_bootstrap(monkeypatch):
    monkeypatch.setattr(service, "stop", lambda: None)
    launch = Mock()
    monkeypatch.setattr(service, "launchctl", launch)
    monkeypatch.setattr(service, "executable", lambda: ["/test/bin", "run"])
    service.install()
    assert launch.call_args[0][0] == "bootstrap"
    assert not service.agent_path().exists()


def test_stop_unloads_before_removing(monkeypatch):
    path = service.agent_path()
    path.parent.mkdir(parents=True)
    path.write_text("test")
    launch = Mock(side_effect=[Mock(returncode=0), Mock(returncode=1)])
    monkeypatch.setattr(service, "launchctl", launch)
    service.stop()
    assert launch.call_args_list[0][0][0] == "bootout"
    assert not path.exists()


def test_failed_stop_keeps_registration(monkeypatch):
    path = service.agent_path()
    path.parent.mkdir(parents=True)
    path.write_text("test")
    monkeypatch.setattr(service, "launchctl", Mock(return_value=Mock(returncode=0)))
    with pytest.raises(ConfigError):
        service.stop()
    assert path.exists()


def test_redacts_token_and_tracebacks():
    token = "123456:" + "a" * 35
    record = logging.LogRecord("test", logging.ERROR, "", 0, "url /bot%s/getMe", (token,), None)
    assert token not in Redactor().format(record)
    assert "[REDACTED]" in Redactor().format(record)


def test_upgrade_removes_persistent_registration_without_starting(monkeypatch):
    path = service.agent_path()
    path.parent.mkdir(parents=True)
    path.write_text("old registration")
    launch = Mock()
    monkeypatch.setattr(service, "launchctl", launch)
    service.migrate_registration()
    assert not path.exists()
    launch.assert_not_called()
