"""Remote CI regressions for credential selection and real transport interruption."""

import asyncio
import json
import os
from unittest.mock import Mock

import pytest

from prbot import provider_check
from prbot.acceptance_network import NetworkGate
from prbot.config import ConfigError


def test_acceptance_reads_auth_from_infisical_and_drops_empty_optional_settings(monkeypatch):
    values = {"PRBOT_ACCEPTANCE_CODEX_AUTH": '{"tokens":{"access_token":"private"}}'}
    loader = Mock(return_value=values)
    monkeypatch.setattr(provider_check, "require_secrets", loader)
    monkeypatch.setenv("PRBOT_ACCEPTANCE_BOOTSTRAP", "")
    monkeypatch.setenv("PRBOT_ACCEPTANCE_CODEX_AUTH", "")
    monkeypatch.setenv("INFISICAL_URL", "")
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "organization-identity")
    actual, auth = provider_check.acceptance_credentials()
    assert actual is values and auth == values["PRBOT_ACCEPTANCE_CODEX_AUTH"]
    assert "INFISICAL_URL" not in os.environ
    assert os.environ["INFISICAL_CLIENT_ID"] == "organization-identity"
    assert "PRBOT_ACCEPTANCE_CODEX_AUTH" not in os.environ
    loader.assert_called_once_with("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")


def test_acceptance_preserves_explicit_secret_override_and_legacy_bootstrap(monkeypatch):
    monkeypatch.setattr(provider_check, "require_secrets", Mock(return_value={}))
    # Register the key with pytest before the loader intentionally changes it.
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "initial-identity")
    monkeypatch.setenv("PRBOT_ACCEPTANCE_CODEX_AUTH", "explicit-login")
    monkeypatch.setenv("PRBOT_ACCEPTANCE_BOOTSTRAP", json.dumps({"INFISICAL_CLIENT_ID": "legacy"}))
    assert provider_check.acceptance_credentials()[1] == "explicit-login"
    assert os.environ["INFISICAL_CLIENT_ID"] == "legacy"


async def test_acceptance_refuses_local_execution_before_loading_credentials(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    loader = Mock()
    monkeypatch.setattr(provider_check, "acceptance_credentials", loader)
    with pytest.raises(ConfigError, match="remote CI"):
        await provider_check.check()
    loader.assert_not_called()


async def test_network_gate_cuts_existing_tunnel_and_restores_new_connections():
    async def echo(reader, writer):
        try:
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gate = NetworkGate(f"https://127.0.0.1:{port}")

    async def connect():
        proxy_port = int(gate.proxy.rsplit(":", 1)[1])
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        return reader, writer

    try:
        async with gate.serve():
            reader, writer = await connect()
            try:
                assert b"200" in await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
                writer.write(b"real-bytes")
                await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(10), 2) == b"real-bytes"
                gate.disconnect()
                assert await asyncio.wait_for(reader.read(), 2) == b""
            finally:
                writer.close()
                await writer.wait_closed()
            reader, writer = await connect()
            try:
                assert await asyncio.wait_for(reader.read(), 2) == b""
            finally:
                writer.close()
                await writer.wait_closed()
            gate.connected = True
            reader, writer = await connect()
            try:
                assert b"200" in await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
                writer.write(b"recovered")
                await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(9), 2) == b"recovered"
            finally:
                writer.close()
                await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
