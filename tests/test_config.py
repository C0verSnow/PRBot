import json
from unittest.mock import Mock

import httpx
import pytest

from prbot.config import Config, ConfigError, read_env, save_settings
from prbot.execution_setup import codex_login
from prbot.paths import data_dir

TOKEN = "123456:" + "a" * 35


def test_operational_settings_only_come_from_infisical(monkeypatch, tmp_path):
    from prbot import secrets

    path = tmp_path / ".env"
    path.write_text("TELEGRAM_BOT_TOKEN=ignored\nINFISICAL_PROJECT_ID=project\n")
    path.chmod(0o600)
    assert read_env() == {"INFISICAL_PROJECT_ID": "project"}
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ignored")
    monkeypatch.setattr(secrets, "load_secrets", lambda: {"TELEGRAM_BOT_TOKEN": TOKEN})
    assert Config.load().token == TOKEN
    assert TOKEN not in repr(Config.load())


@pytest.mark.parametrize(
    "key,value",
    [
        ("PRBOT_POLL_TIMEOUT", "0"),
        ("PRBOT_POLL_TIMEOUT", "11"),
        ("PRBOT_POLL_TIMEOUT", "bad"),
        ("PRBOT_LOG_LEVEL", "TRACE"),
    ],
)
def test_invalid_remote_config(monkeypatch, key, value):
    monkeypatch.setattr(
        "prbot.secrets.load_secrets", lambda: {"TELEGRAM_BOT_TOKEN": TOKEN, key: value}
    )
    with pytest.raises(ConfigError):
        Config.load()


def test_missing_secrets_reports_names_without_values(monkeypatch):
    monkeypatch.setattr("prbot.secrets.load_secrets", lambda: {"SECRET": "do-not-echo"})
    monkeypatch.setattr("prbot.secrets.bootstrap", lambda: {"INFISICAL_PROJECT_ID": "p"})
    with pytest.raises(ConfigError) as error:
        Config.load()
    assert "TELEGRAM_BOT_TOKEN" in str(error.value)
    assert "do-not-echo" not in str(error.value)


def test_settings_migration_removes_legacy_secret_and_policy_fields():
    path = data_dir() / "config.json"
    path.write_text(json.dumps({"TELEGRAM_BOT_TOKEN": TOKEN, "PRBOT_AUTOSTART": "true"}))
    path.chmod(0o600)
    save_settings({"TELEGRAM_BOT_TOKEN": TOKEN})
    assert json.loads(path.read_text()) == {}
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("value", ["", "bad", 123, True, {}])
def test_invalid_token(value):
    from prbot.config import validate_token

    with pytest.raises(ConfigError):
        validate_token(value)


def test_bootstrap_file_rejects_public_permissions(tmp_path):
    path = tmp_path / ".env"
    path.write_text("INFISICAL_CLIENT_SECRET=never-echo")
    path.chmod(0o644)
    with pytest.raises(ConfigError) as error:
        read_env()
    assert "never-echo" not in str(error.value)


def test_selected_path_prepares_only_bootstrap_and_preserves_existing_env(tmp_path):
    from prbot.config import prepare_env_directory, workspace_directory
    from prbot.task_config import select_work_root

    folder = tmp_path / "chosen"
    folder.mkdir()
    select_work_root(str(folder))
    assert workspace_directory() == folder / "PRBot-workspaces"
    path = workspace_directory() / ".env"
    path.write_text("INFISICAL_CLIENT_ID=existing\n")
    prepare_env_directory()
    assert path.read_text() == "INFISICAL_CLIENT_ID=existing\n"
    with pytest.raises(ConfigError):
        select_work_root(str(folder / "missing"))
    assert workspace_directory() == folder / "PRBot-workspaces"


def test_env_directory_rejects_symlinks(tmp_path):
    from prbot.config import prepare_env_directory

    external = tmp_path / "external"
    external.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(external, target_is_directory=True)
    with pytest.raises(ConfigError):
        prepare_env_directory(link)
    assert not (external / ".env").exists()


@pytest.mark.parametrize("content", [None, "{broken", "{}", '{"tokens":{"id_token":"x"}}'])
def test_bad_codex_login_has_actionable_feedback(tmp_path, content, monkeypatch):
    monkeypatch.setattr("prbot.execution_setup._override", None)
    if content is not None:
        directory = tmp_path / ".codex"
        directory.mkdir()
        (directory / "auth.json").write_text(content)
    with pytest.raises(ConfigError, match="Sign in with Codex"):
        codex_login()


def test_codex_login_keeps_full_auth_and_id_edits_in_memory(tmp_path, monkeypatch):
    monkeypatch.setattr("prbot.execution_setup._override", None)
    directory = tmp_path / ".codex"
    directory.mkdir()
    original = json.dumps(
        {"tokens": {"id_token": "original-id", "access_token": "original-access"}}
    )
    path = directory / "auth.json"
    path.write_text(original)
    auth = json.loads(codex_login("edited-id"))
    assert auth["tokens"] == {"id_token": "edited-id", "access_token": "original-access"}
    assert path.read_text() == original


def test_infisical_requests_are_scoped_and_failure_never_echoes_credentials(monkeypatch):
    from prbot import secrets

    monkeypatch.setattr(secrets, "_cache", None)
    monkeypatch.setattr(
        secrets,
        "bootstrap",
        lambda: {
            "INFISICAL_CLIENT_ID": "client",
            "INFISICAL_CLIENT_SECRET": "private-secret",
            "INFISICAL_PROJECT_ID": "project",
        },
    )
    responses = [
        httpx.Response(
            200, json={"accessToken": "temporary"}, request=httpx.Request("POST", "https://x")
        ),
        httpx.Response(
            200,
            json={"secrets": [{"secretKey": "TELEGRAM_BOT_TOKEN", "secretValue": TOKEN}]},
            request=httpx.Request("GET", "https://x"),
        ),
    ]
    client = Mock(post=Mock(return_value=responses[0]), get=Mock(return_value=responses[1]))
    context = Mock(__enter__=Mock(return_value=client), __exit__=Mock(return_value=False))
    monkeypatch.setattr(secrets.httpx, "Client", lambda **kwargs: context)
    assert secrets.load_secrets(refresh=True)["TELEGRAM_BOT_TOKEN"] == TOKEN
    assert client.get.call_args.kwargs["params"]["projectId"] == "project"
    client.post.side_effect = httpx.ConnectError("private-secret")
    with pytest.raises(ConfigError) as error:
        secrets.load_secrets(refresh=True)
    assert "private-secret" not in str(error.value)
