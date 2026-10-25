"""Issue #25 regression scenarios, executed only by remote CI."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from prbot.sdk_worker import catalog


def model(slug, modalities=None):
    return SimpleNamespace(
        model=slug, display_name=slug, hidden=False,
        input_modalities=modalities, is_default=False,
    )


def test_catalog_reads_all_pages_instead_of_discarding_first_page():
    first = SimpleNamespace(data=[model("first")], next_cursor="page-2")
    second = SimpleNamespace(data=[model("first"), model("second")], next_cursor=None)
    client = Mock()
    client.models.return_value = first
    client._client.request.return_value = second
    assert [item["id"] for item in catalog(client)] == ["first", "second"]
    client._client.request.assert_called_once_with(
        "model/list", {"includeHidden": False, "cursor": "page-2"},
        response_model=type(first),
    )


def test_empty_catalog_reports_reason_instead_of_silent_default():
    client = Mock()
    client.models.return_value = SimpleNamespace(data=[], next_cursor=None)
    with pytest.raises(RuntimeError, match="No visible text models"):
        catalog(client)


def test_repeated_cursor_fails_instead_of_looping_forever():
    page = SimpleNamespace(data=[model("first")], next_cursor="same")
    client = Mock()
    client.models.return_value = page
    client._client.request.return_value = page
    with pytest.raises(RuntimeError, match="cursor"):
        catalog(client)
    client._client.request.assert_called_once()


def test_pinned_sdk_supports_catalog_pagination_seam():
    import inspect

    from openai_codex.client import CodexClient
    from openai_codex.generated.v2_all import ModelListResponse

    assert "response_model" in inspect.signature(CodexClient.request).parameters
    page = ModelListResponse.model_validate({"data": [], "nextCursor": "page-2"})
    assert page.next_cursor == "page-2"
