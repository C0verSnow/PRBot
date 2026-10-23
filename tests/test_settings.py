"""Settings regression cases executed by the existing remote macOS CI only."""

import base64
import json
from collections import UserDict
from pathlib import Path
from unittest.mock import Mock

import pytest

from prbot.config import ConfigError, bootstrap_form, read_env, save_bootstrap_form
from prbot.desktop import native_request, settings_request
from prbot.execution_setup import codex_login, fetch_codex_id_token


def id_token():
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return encode({"alg": "RS256"}) + "." + encode({"sub": "test-only"}) + ".c2lnbmF0dXJl"


def write_auth(tmp_path, value):
    path = tmp_path / ".codex/auth.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def fields():
    return {
        "INFISICAL_URL": "https://app.infisical.com",
        "INFISICAL_CLIENT_ID": "test-client",
        "INFISICAL_CLIENT_SECRET": "test-secret-only",
        "INFISICAL_PROJECT_ID": "test-project",
        "INFISICAL_ENVIRONMENT": "dev",
        "INFISICAL_SECRET_PATH": "/",
    }


def test_native_bridge_copies_nested_mapping_before_worker_thread():
    # Foundation dictionaries implement mapping APIs but are not Python dicts.
    native = UserDict({"action": "save_configuration", "configuration": UserDict(fields())})
    result = native_request(native)
    assert result == {"action": "save_configuration", "configuration": fields()}
    assert type(result["configuration"]) is dict


def test_native_bridge_invalid_object_returns_error_instead_of_crashing():
    with pytest.raises(ConfigError, match="Invalid request data"):
        native_request({"configuration": object()})


def test_fetch_reads_native_id_without_touching_login_or_infisical(tmp_path, monkeypatch):
    from prbot import execution_setup

    token = id_token()
    path = write_auth(tmp_path, {"tokens": {"id_token": token, "access_token": "access-only"}})
    original = path.read_bytes()
    codex_login("manual-id")
    session = (original.decode(), json.dumps({"tokens": {"id_token": "refreshed-id"}}))
    monkeypatch.setattr(execution_setup, "_session_auth", session)
    network = Mock(side_effect=AssertionError("must not contact Infisical"))
    monkeypatch.setattr("prbot.secrets.load_secrets", network)
    message, result = settings_request("fetch_codex_id_token", {})
    assert result == {"id_token_result": {"token": token, "applied": False}}
    assert token not in message and "access-only" not in json.dumps(result)
    assert execution_setup._override == "manual-id"
    assert execution_setup._session_auth == session
    assert path.read_bytes() == original
    network.assert_not_called()


@pytest.mark.parametrize(
    "auth",
    [
        None,
        [],
        {},
        {"tokens": None},
        {"tokens": {"access_token": id_token()}},
        {"tokens": {"id_token": "plain-secret"}},
        {"tokens": {"id_token": 123}},
        {"tokens": {"id_token": "a.b.c"}},
        {"tokens": {"id_token": "e30.e30.c2ln"}},
    ],
)
def test_fetch_missing_or_invalid_never_returns_other_token(tmp_path, auth):
    if auth is not None:
        write_auth(tmp_path, auth)
    with pytest.raises(ConfigError) as error:
        fetch_codex_id_token()
    assert "plain-secret" not in str(error.value)
    assert id_token() not in str(error.value)


@pytest.mark.parametrize("failure", [PermissionError("private-value"), OSError("private-value")])
def test_fetch_read_failure_is_actionable_and_redacted(monkeypatch, failure):
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=failure))
    with pytest.raises(ConfigError, match="read permissions") as error:
        fetch_codex_id_token()
    assert "private-value" not in str(error.value)


def test_fetch_bad_json_is_redacted(tmp_path):
    path = write_auth(tmp_path, {})
    path.write_text('{"tokens":"private-invalid-json')
    with pytest.raises(ConfigError) as error:
        fetch_codex_id_token()
    assert "private-invalid-json" not in str(error.value)


def test_manual_use_is_independent_of_infisical(tmp_path, monkeypatch):
    write_auth(tmp_path, {"tokens": {"id_token": id_token(), "access_token": "access-only"}})
    monkeypatch.setattr("prbot.secrets.load_secrets", Mock(side_effect=AssertionError))
    message, result = settings_request("use_id_token", {"text": "manual-id"})
    assert result["id_token_result"] == {"token": "manual-id", "applied": True}
    assert "manual-id" not in message


def test_configuration_roundtrip_private_and_secret_never_exposed(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# keep comment\nOTHER_TOOL=value\nINFISICAL_CLIENT_SECRET=old-secret\n")
    path.chmod(0o600)
    values = fields()
    values["INFISICAL_CLIENT_SECRET"] = ""
    message, reply = settings_request(
        "save_configuration", {"configuration": values, "configuration_path": str(path)}
    )
    form = reply["configuration"]
    assert form == bootstrap_form()
    assert form["client_secret_set"]
    assert "INFISICAL_CLIENT_SECRET" not in form["values"]
    assert "old-secret" not in json.dumps(reply) + message
    assert "Restart PRBot" in message
    assert path.stat().st_mode & 0o777 == 0o600
    assert "# keep comment\nOTHER_TOOL=value\n" in path.read_text()
    assert read_env()["INFISICAL_CLIENT_SECRET"] == "old-secret"
    values["INFISICAL_CLIENT_SECRET"] = "replacement-secret"
    save_bootstrap_form(values, str(path))
    assert read_env()["INFISICAL_CLIENT_SECRET"] == "replacement-secret"
    assert bootstrap_form()["client_secret_set"]


def test_all_configuration_fields_save_and_reload_without_mixing_values(tmp_path):
    path = tmp_path / ".env"
    values = fields() | {
        "INFISICAL_URL": "https://infisical.example.test",
        "INFISICAL_ENVIRONMENT": "staging",
        "INFISICAL_SECRET_PATH": "/prbot",
    }
    settings_request(
        "save_configuration", {"configuration": values, "configuration_path": str(path)}
    )
    assert read_env() == values
    _, reply = settings_request("configuration", {})
    assert reply["configuration"]["values"] == {
        key: value for key, value in values.items() if key != "INFISICAL_CLIENT_SECRET"
    }
    assert reply["configuration"]["client_secret_set"]
    assert values["INFISICAL_CLIENT_SECRET"] not in json.dumps(reply)
    updated = values | {
        "INFISICAL_CLIENT_ID": "updated-client",
        "INFISICAL_CLIENT_SECRET": "",
        "INFISICAL_PROJECT_ID": "updated-project",
        "INFISICAL_ENVIRONMENT": "production",
        "INFISICAL_SECRET_PATH": "/updated",
    }
    settings_request(
        "save_configuration", {"configuration": updated, "configuration_path": str(path)}
    )
    assert read_env() == updated | {"INFISICAL_CLIENT_SECRET": values["INFISICAL_CLIENT_SECRET"]}
    _, reloaded = settings_request("configuration", {})
    assert reloaded["configuration"]["values"] == {
        key: value for key, value in updated.items() if key != "INFISICAL_CLIENT_SECRET"
    }
    assert reloaded["configuration"]["client_secret_set"]


def test_configuration_preserves_process_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "environment-project")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "environment-secret")
    values = fields()
    save_bootstrap_form(values, str(tmp_path / ".env"))
    form = bootstrap_form()
    assert form["values"]["INFISICAL_PROJECT_ID"] == "environment-project"
    assert form["environment_overrides"] == ["INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID"]
    assert "environment-secret" not in json.dumps(form)
    assert read_env()["INFISICAL_PROJECT_ID"] == "test-project"


def test_blank_secret_retains_original_quoted_line_exactly(tmp_path):
    path = tmp_path / ".env"
    line = 'INFISICAL_CLIENT_SECRET="  original-secret  "\n'
    path.write_text(line)
    path.chmod(0o600)
    values = fields()
    values["INFISICAL_CLIENT_SECRET"] = ""
    save_bootstrap_form(values, str(path))
    assert line in path.read_text()
    assert read_env()["INFISICAL_CLIENT_SECRET"] == "  original-secret  "


@pytest.mark.parametrize(
    "key,value",
    [
        ("INFISICAL_URL", "ftp://private-host"),
        ("INFISICAL_URL", "https://"),
        ("INFISICAL_URL", "https://user:private-secret@host"),
        ("INFISICAL_URL", "https://host:99999"),
        ("INFISICAL_URL", "https://host?secret=x"),
        ("INFISICAL_URL", "https://host/#secret"),
        ("INFISICAL_CLIENT_ID", ""),
        ("INFISICAL_CLIENT_SECRET", ""),
        ("INFISICAL_PROJECT_ID", ""),
        ("INFISICAL_ENVIRONMENT", ""),
        ("INFISICAL_SECRET_PATH", "relative"),
        ("INFISICAL_CLIENT_SECRET", "private-secret\nINJECT=value"),
        ("INFISICAL_CLIENT_SECRET", "private-secret\u2028INJECT=value"),
        ("INFISICAL_CLIENT_SECRET", "'private-secret'"),
    ],
)
def test_configuration_validation_failure_preserves_original(tmp_path, key, value):
    path = tmp_path / ".env"
    path.write_text("# untouched\n")
    path.chmod(0o600)
    values = fields()
    values[key] = value
    with pytest.raises(ConfigError) as error:
        save_bootstrap_form(values, str(path))
    assert "private-secret" not in str(error.value)
    assert path.read_text() == "# untouched\n"


def test_http_configuration_is_supported(tmp_path):
    values = fields()
    values["INFISICAL_URL"] = "http://127.0.0.1:8080"
    save_bootstrap_form(values, str(tmp_path / ".env"))
    assert bootstrap_form()["values"]["INFISICAL_URL"] == "http://127.0.0.1:8080"


def test_configuration_write_failure_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# untouched\n")
    path.chmod(0o600)
    monkeypatch.setattr(
        "prbot.config.os.replace", Mock(side_effect=PermissionError("test-secret-only"))
    )
    with pytest.raises(ConfigError, match="write permissions") as error:
        save_bootstrap_form(fields(), str(path))
    assert "test-secret-only" not in str(error.value)
    assert path.read_text() == "# untouched\n"
    assert not list(tmp_path.glob(".prbot-env-*"))


def test_configuration_stale_workspace_refused(tmp_path):
    with pytest.raises(ConfigError, match="directory changed"):
        save_bootstrap_form(fields(), str(tmp_path / "old/.env"))
    assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_configuration_unsafe_file_refused(tmp_path, symlink):
    path = tmp_path / ".env"
    if symlink:
        target = tmp_path / "external"
        target.write_text("INFISICAL_CLIENT_SECRET=private-secret\n")
        path.symlink_to(target)
    else:
        path.write_text("INFISICAL_CLIENT_SECRET=private-secret\n")
        path.chmod(0o644)
    with pytest.raises(ConfigError):
        save_bootstrap_form(fields(), str(path))
    assert "private-secret" in path.read_text()


def test_configuration_read_failure_is_safe(monkeypatch):
    monkeypatch.setattr("prbot.config.read_env", Mock(side_effect=ConfigError("Cannot read .env.")))
    with pytest.raises(ConfigError, match="Cannot read"):
        settings_request("configuration", {})
