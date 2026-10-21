import json

import pytest

from prbot.config import Config, ConfigError, save_settings
from prbot.paths import data_dir

TOKEN = "123456:" + "a" * 35


def test_keychain_defaults(monkeypatch):
    monkeypatch.setattr("prbot.config.keychain_token", lambda: TOKEN)
    config = Config.load()
    assert config.poll_timeout == 30
    assert config.autostart is False
    assert TOKEN not in repr(config)


def test_env_overrides_file(monkeypatch):
    path = data_dir() / "config.json"
    path.write_text(json.dumps({"TELEGRAM_BOT_TOKEN": TOKEN, "PRBOT_POLL_TIMEOUT": 10}))
    path.chmod(0o600)
    monkeypatch.setenv("PRBOT_POLL_TIMEOUT", "20")
    monkeypatch.setenv("PRBOT_AUTOSTART", "true")
    assert Config.load().poll_timeout == 20
    assert Config.load().autostart


@pytest.mark.parametrize("value", ["", "bad", "123:tiny"])
def test_missing_or_invalid_token(monkeypatch, value):
    monkeypatch.setattr("prbot.config.keychain_token", lambda: value)
    with pytest.raises(ConfigError) as error:
        Config.load()
    assert "BotFather" in str(error.value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("PRBOT_POLL_TIMEOUT", "0"),
        ("PRBOT_POLL_TIMEOUT", "51"),
        ("PRBOT_POLL_TIMEOUT", "xx"),
        ("PRBOT_AUTOSTART", "yes"),
        ("PRBOT_LOG_LEVEL", "TRACE"),
    ],
)
def test_invalid_settings(monkeypatch, key, value):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv(key, value)
    with pytest.raises(ConfigError):
        Config.load()


def test_settings_permissions_and_no_token_copy():
    save_settings({"PRBOT_AUTOSTART": "true", "TELEGRAM_BOT_TOKEN": TOKEN})
    path = data_dir() / "config.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert TOKEN not in path.read_text()
    path.chmod(0o644)
    with pytest.raises(ConfigError):
        Config.load()


def test_corrupt_settings():
    path = data_dir() / "config.json"
    path.write_text("{broken")
    path.chmod(0o600)
    with pytest.raises(ConfigError):
        Config.load()


@pytest.mark.parametrize("value", [123456, True, ["invalid"], {"bad": "token"}])
def test_non_string_token_is_configuration_error(value):
    from prbot.config import validate_token

    with pytest.raises(ConfigError):
        validate_token(value)
