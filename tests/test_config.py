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


def test_dotenv_supplies_config_and_credentials(tmp_path, monkeypatch):
    from prbot.task_config import TaskConfig, credential

    path = tmp_path / ".env"
    path.write_text(
        f"TELEGRAM_BOT_TOKEN={TOKEN}\nGITHUB_TOKEN=test-secret\n"
        "PRBOT_ALLOWED_USERS=123\nPRBOT_ALLOWED_REPOS=owner/repo\n"
        "PRBOT_POLL_TIMEOUT=12\n"
    )
    path.chmod(0o600)
    assert Config.load().token == TOKEN
    assert Config.load().poll_timeout == 12
    assert TaskConfig.load().enabled
    assert credential("GITHUB_TOKEN") == "test-secret"
    monkeypatch.setenv("PRBOT_POLL_TIMEOUT", "15")
    assert Config.load().poll_timeout == 15
    assert "test-secret" not in repr(TaskConfig.load())


def test_dotenv_empty_values_keep_keychain_defaults(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("TELEGRAM_BOT_TOKEN=\nPRBOT_POLL_TIMEOUT=\n")
    path.chmod(0o600)
    monkeypatch.setattr("prbot.config.keychain_token", lambda: TOKEN)
    assert Config.load().token == TOKEN
    assert Config.load().poll_timeout == 30


@pytest.mark.parametrize(
    "text,mode",
    [
        ("TELEGRAM_BOT_TOKEN=secret", 0o644),
        ('PRBOT_ALLOWED_REPOS="unclosed', 0o600),
    ],
)
def test_dotenv_errors_never_include_values(tmp_path, text, mode):
    from prbot.config import read_env

    path = tmp_path / ".env"
    path.write_text(text)
    path.chmod(mode)
    with pytest.raises(ConfigError) as error:
        read_env()
    assert "secret" not in str(error.value)
    assert "unclosed" not in str(error.value)


def test_dotenv_quotes_unknown_keys_and_no_expansion(tmp_path):
    from prbot.config import read_env

    path = tmp_path / ".env"
    path.write_text(
        '# comment\nPRBOT_ALLOWED_REPOS="literal/$HOME"\n'
        "GITHUB_TOKEN='literal-token'\nUNKNOWN_KEY=ignored\n"
    )
    path.chmod(0o600)
    assert read_env() == {"PRBOT_ALLOWED_REPOS": "literal/$HOME", "GITHUB_TOKEN": "literal-token"}


def test_selected_work_path_creates_env_and_preserves_user_values(tmp_path, monkeypatch):
    from prbot.config import prepare_env_directory, workspace_directory
    from prbot.task_config import select_work_root

    selected = tmp_path / "chosen"
    selected.mkdir()
    select_work_root(str(selected))
    folder = selected / "PRBot-workspaces"
    assert workspace_directory() == folder
    assert (folder / ".env.example").is_file()
    path = folder / ".env"
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\n")
    prepare_env_directory()
    assert path.read_text() == f"TELEGRAM_BOT_TOKEN={TOKEN}\n"
    monkeypatch.setattr("prbot.config.env_path", lambda: workspace_directory() / ".env")
    assert Config.load().token == TOKEN
    second = tmp_path / "second"
    second.mkdir()
    select_work_root(str(second))
    assert workspace_directory() == second / "PRBot-workspaces"
    assert TOKEN not in (workspace_directory() / ".env").read_text()
    assert path.read_text() == f"TELEGRAM_BOT_TOKEN={TOKEN}\n"


def test_env_directory_rejects_symlink_before_writing(tmp_path):
    from prbot.config import prepare_env_directory

    external = tmp_path / "external"
    external.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(external, target_is_directory=True)
    with pytest.raises(ConfigError):
        prepare_env_directory(link)
    assert not (external / ".env").exists()
