import logging
import plistlib
from unittest.mock import Mock

import pytest

from prbot import service
from prbot.config import ConfigError
from prbot.logging_config import Redactor


def test_autostart_plist_has_no_credentials(monkeypatch):
    monkeypatch.setattr(service, "stop", lambda: None)
    monkeypatch.setattr(service, "launchctl", Mock())
    monkeypatch.setattr(service, "executable", lambda: ["/Applications/PRBot.app/bin/PRBot", "run"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "a" * 35)
    service.install(True)
    spec = plistlib.loads(service.agent_path().read_bytes())
    assert spec["KeepAlive"] == {"SuccessfulExit": False}
    assert spec["ThrottleInterval"] == 30
    assert "TELEGRAM_BOT_TOKEN" not in str(spec)
    assert service.agent_path().stat().st_mode & 0o777 == 0o600


def test_no_autostart_removes_plist_after_bootstrap(monkeypatch):
    monkeypatch.setattr(service, "stop", lambda: None)
    launch = Mock()
    monkeypatch.setattr(service, "launchctl", launch)
    monkeypatch.setattr(service, "executable", lambda: ["/test/bin", "run"])
    service.install(False)
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
