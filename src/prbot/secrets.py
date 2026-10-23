"""Infisical is the only source of operational settings and secrets."""

import time
from threading import Lock

import httpx

from prbot.config import ConfigError, bootstrap_values, validate_infisical_url

_lock = Lock()
_cache = None
_expires = 0
_cache_key = None


def bootstrap(configuration=None):
    values = bootstrap_values(configuration)
    required = ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ConfigError(
            "Missing Infisical connection settings: "
            + ", ".join(missing)
            + ". Open Settings > Configuration or edit .env in the configuration directory."
        )
    return values


def load_secrets(refresh=False, configuration=None):
    global _cache, _expires, _cache_key
    with _lock:
        settings = bootstrap() if configuration is None else bootstrap(configuration)
        identity = tuple(sorted(settings.items()))
        if (
            not refresh
            and _cache is not None
            and _cache_key == identity
            and time.monotonic() < _expires
        ):
            return dict(_cache)
        url = settings.get("INFISICAL_URL", "https://app.infisical.com").rstrip("/")
        validate_infisical_url(url)
        try:
            with httpx.Client(base_url=url, timeout=20, follow_redirects=False) as client:
                response = client.post(
                    "/api/v1/auth/universal-auth/login",
                    json={
                        "clientId": settings["INFISICAL_CLIENT_ID"],
                        "clientSecret": settings["INFISICAL_CLIENT_SECRET"],
                    },
                )
                response.raise_for_status()
                token = response.json()["accessToken"]
                response = client.get(
                    "/api/v4/secrets",
                    headers={
                        "Authorization": "Bearer " + token,
                    },
                    params={
                        "projectId": settings["INFISICAL_PROJECT_ID"],
                        "environment": settings.get("INFISICAL_ENVIRONMENT", "dev"),
                        "secretPath": settings.get("INFISICAL_SECRET_PATH", "/"),
                        "include_imports": "false",
                    },
                )
                response.raise_for_status()
                values = {s["secretKey"]: s["secretValue"] for s in response.json()["secrets"]}
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise ConfigError(
                "Infisical unavailable. Check the connection settings, network "
                "and machine identity permissions, then retry."
            ) from None
        # Console copy/paste can retain shell quotes around the REST endpoint.
        endpoint = values.get("UPSTASH_REDIS_REST_URL", "").strip()
        if len(endpoint) >= 2 and endpoint[0] in {"'", '"'} and endpoint[-1] == endpoint[0]:
            endpoint = endpoint[1:-1].strip()
        if endpoint:
            values["UPSTASH_REDIS_REST_URL"] = endpoint
        if not values.get("TELEGRAM_BOT_TOKEN") and values.get("PR_BOT_TOKEN"):
            values["TELEGRAM_BOT_TOKEN"] = values["PR_BOT_TOKEN"]
        _cache, _expires, _cache_key = values, time.monotonic() + 60, identity
        return dict(values)


def require_secrets(*keys, configuration=None):
    values = load_secrets() if configuration is None else load_secrets(configuration=configuration)
    missing = [key for key in keys if not values.get(key)]
    if missing:
        cfg = bootstrap() if configuration is None else bootstrap(configuration)
        raise ConfigError(
            "Missing secrets in Infisical: "
            + ", ".join(missing)
            + ". Add them to project "
            + cfg["INFISICAL_PROJECT_ID"]
            + " / "
            + cfg.get("INFISICAL_ENVIRONMENT", "dev")
            + " / "
            + cfg.get("INFISICAL_SECRET_PATH", "/")
            + "."
        )
    return values


def clear_cache():
    global _cache, _expires, _cache_key
    with _lock:
        _cache, _expires, _cache_key = None, 0, None
