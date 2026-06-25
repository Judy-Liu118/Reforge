"""Unit tests for LLMClient retry/backoff and hook emission."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

import reforge.observability.llm_events as _events
from reforge.models.adapters.llm_client import LLMClient, set_hook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.choices[0].message.content = text
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


def _make_client(responses=None, side_effects=None):
    """Return (LLMClient, mock_create).

    responses     — list of text strings returned in order
    side_effects  — list of exceptions/values for side_effect
    """
    client = LLMClient.__new__(LLMClient)
    client._model = "test-model"
    client._logger = MagicMock()

    mock_create = MagicMock()
    if side_effects is not None:
        mock_create.side_effect = side_effects
    elif responses is not None:
        mock_create.side_effect = [_make_response(r) for r in responses]

    inner = MagicMock()
    inner.chat.completions.create = mock_create
    client._client = inner
    return client, mock_create


# ---------------------------------------------------------------------------
# Basic success
# ---------------------------------------------------------------------------


def test_chat_returns_stripped_text():
    client, _ = _make_client(responses=["  hello world  "])
    assert client.chat("sys", "user") == "hello world"


def test_chat_returns_empty_string_on_none_content():
    resp = MagicMock()
    resp.choices[0].message.content = None
    resp.usage = None
    client = LLMClient.__new__(LLMClient)
    client._model = "m"
    client._logger = MagicMock()
    inner = MagicMock()
    inner.chat.completions.create.return_value = resp
    client._client = inner
    assert client.chat("s", "u") == ""


# ---------------------------------------------------------------------------
# Retry on transient errors
# ---------------------------------------------------------------------------


def _rate_limit_error() -> RateLimitError:
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {}
    return RateLimitError("rate limited", response=mock_resp, body=None)


def _timeout_error() -> APITimeoutError:
    return APITimeoutError(request=MagicMock())


def _conn_error() -> APIConnectionError:
    return APIConnectionError(request=MagicMock())


@patch("reforge.models.adapters.llm_client.time.sleep")
@patch("reforge.config.config")
def test_retries_on_rate_limit(mock_config, mock_sleep):
    mock_config.max_retry = 2
    mock_config.llm_api_key = "key"
    mock_config.llm_base_url = "http://x"
    mock_config.llm_model = "m"

    ok_resp = _make_response("ok")
    client, mock_create = _make_client(side_effects=[
        _rate_limit_error(), _rate_limit_error(), ok_resp
    ])
    client._client.chat.completions.create = mock_create

    with patch.object(_events, "_hook", None):
        with patch("reforge.models.adapters.llm_client.config", mock_config):
            result = client.chat("sys", "user")

    assert result == "ok"
    assert mock_create.call_count == 3
    assert mock_sleep.call_count == 2


@patch("reforge.models.adapters.llm_client.time.sleep")
@patch("reforge.config.config")
def test_retries_on_timeout(mock_config, mock_sleep):
    mock_config.max_retry = 1
    mock_config.llm_api_key = "key"

    ok_resp = _make_response("done")
    client, mock_create = _make_client(side_effects=[
        _timeout_error(), ok_resp
    ])

    with patch("reforge.models.adapters.llm_client.config", mock_config):
        result = client.chat("sys", "user")

    assert result == "done"
    assert mock_create.call_count == 2


@patch("reforge.models.adapters.llm_client.time.sleep")
@patch("reforge.config.config")
def test_retries_on_connection_error(mock_config, mock_sleep):
    mock_config.max_retry = 1
    mock_config.llm_api_key = "key"

    ok_resp = _make_response("connected")
    client, mock_create = _make_client(side_effects=[
        _conn_error(), ok_resp
    ])

    with patch("reforge.models.adapters.llm_client.config", mock_config):
        result = client.chat("sys", "user")

    assert result == "connected"
    assert mock_create.call_count == 2


@patch("reforge.models.adapters.llm_client.time.sleep")
@patch("reforge.config.config")
def test_exhausted_retries_raises(mock_config, mock_sleep):
    mock_config.max_retry = 2
    mock_config.llm_api_key = "key"

    client, mock_create = _make_client(side_effects=[
        _rate_limit_error(), _rate_limit_error(), _rate_limit_error()
    ])

    with patch("reforge.models.adapters.llm_client.config", mock_config):
        with pytest.raises(RuntimeError, match="LLM call failed after 3 attempts"):
            client.chat("sys", "user")

    assert mock_create.call_count == 3


# ---------------------------------------------------------------------------
# 5xx retried, 4xx raises immediately
# ---------------------------------------------------------------------------


def _status_error(code: int) -> APIStatusError:
    mock_resp = MagicMock()
    mock_resp.status_code = code
    mock_resp.headers = {}
    return APIStatusError("error", response=mock_resp, body=None)


@patch("reforge.models.adapters.llm_client.time.sleep")
@patch("reforge.config.config")
def test_retries_on_500(mock_config, mock_sleep):
    mock_config.max_retry = 1
    mock_config.llm_api_key = "key"

    ok_resp = _make_response("recovered")
    client, mock_create = _make_client(side_effects=[
        _status_error(503), ok_resp
    ])

    with patch("reforge.models.adapters.llm_client.config", mock_config):
        result = client.chat("sys", "user")

    assert result == "recovered"
    assert mock_create.call_count == 2


@patch("reforge.config.config")
def test_no_retry_on_400(mock_config):
    mock_config.max_retry = 3
    mock_config.llm_api_key = "key"

    client, mock_create = _make_client(side_effects=[_status_error(400)])

    with patch("reforge.models.adapters.llm_client.config", mock_config):
        with pytest.raises(APIStatusError):
            client.chat("sys", "user")

    assert mock_create.call_count == 1  # no retry


# ---------------------------------------------------------------------------
# Exponential backoff timing
# ---------------------------------------------------------------------------


@patch("reforge.models.adapters.llm_client.time.sleep")
@patch("reforge.config.config")
def test_backoff_is_exponential(mock_config, mock_sleep):
    mock_config.max_retry = 3
    mock_config.llm_api_key = "key"

    ok_resp = _make_response("ok")
    client, mock_create = _make_client(side_effects=[
        _rate_limit_error(), _rate_limit_error(), _rate_limit_error(), ok_resp
    ])

    with patch("reforge.models.adapters.llm_client.config", mock_config):
        client.chat("sys", "user")

    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert sleep_calls == [1, 2, 4]  # 2^0, 2^1, 2^2


# ---------------------------------------------------------------------------
# Hook emission
# ---------------------------------------------------------------------------


@patch("reforge.config.config")
def test_hook_called_on_success(mock_config):
    mock_config.max_retry = 0
    mock_config.llm_api_key = "key"

    events: list[tuple[str, dict]] = []

    def hook(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    client, _ = _make_client(responses=["result"])

    with patch.object(_events, "_hook", hook):
        with patch("reforge.models.adapters.llm_client.config", mock_config):
            client.chat("sys", "user")

    types = [e[0] for e in events]
    assert "llm_call_start" in types
    assert "llm_call_complete" in types
    assert "llm_call_error" not in types
    assert "llm_call_retry" not in types


@patch("reforge.models.adapters.llm_client.time.sleep")
@patch("reforge.config.config")
def test_hook_called_on_retry(mock_config, mock_sleep):
    mock_config.max_retry = 1
    mock_config.llm_api_key = "key"

    events: list[tuple[str, dict]] = []

    def hook(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    ok_resp = _make_response("ok")
    client, _ = _make_client(side_effects=[_rate_limit_error(), ok_resp])

    with patch.object(_events, "_hook", hook):
        with patch("reforge.models.adapters.llm_client.config", mock_config):
            client.chat("sys", "user")

    types = [e[0] for e in events]
    assert types.count("llm_call_retry") == 1
    assert "llm_call_complete" in types


@patch("reforge.models.adapters.llm_client.time.sleep")
@patch("reforge.config.config")
def test_hook_called_on_exhaustion(mock_config, mock_sleep):
    mock_config.max_retry = 1
    mock_config.llm_api_key = "key"

    events: list[tuple[str, dict]] = []

    def hook(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    client, _ = _make_client(side_effects=[
        _rate_limit_error(), _rate_limit_error()
    ])

    with patch.object(_events, "_hook", hook):
        with patch("reforge.models.adapters.llm_client.config", mock_config):
            with pytest.raises(RuntimeError):
                client.chat("sys", "user")

    types = [e[0] for e in events]
    assert "llm_call_error" in types


@patch("reforge.config.config")
def test_hook_exception_does_not_propagate(mock_config):
    """A broken hook must never crash the LLM call."""
    mock_config.max_retry = 0
    mock_config.llm_api_key = "key"

    def bad_hook(event_type: str, payload: dict) -> None:
        raise RuntimeError("hook crashed")

    client, _ = _make_client(responses=["fine"])

    with patch.object(_events, "_hook", bad_hook):
        with patch("reforge.models.adapters.llm_client.config", mock_config):
            result = client.chat("sys", "user")

    assert result == "fine"


# ---------------------------------------------------------------------------
# set_hook public API
# ---------------------------------------------------------------------------


def test_set_hook_registers_and_clears():
    original = _events._hook
    try:
        fn = MagicMock()
        set_hook(fn)
        assert _events._hook is fn
        set_hook(None)
        assert _events._hook is None
    finally:
        _events._hook = original


# ---------------------------------------------------------------------------
# Vision codegen wiring — for_vision_codegen() + chat_multimodal()
# ---------------------------------------------------------------------------


class TestForVisionCodegen:
    def test_factory_wires_codegen_vision_config(self, monkeypatch):
        """LLMClient.for_vision_codegen() must use codegen_vision_* fields
        rather than llm_*, so the text codegen LLM stays untouched."""
        from reforge.config import config as _config

        monkeypatch.setattr(_config, "codegen_vision_base_url", "https://vision.example/v1")
        monkeypatch.setattr(_config, "codegen_vision_api_key", "vision-key")
        monkeypatch.setattr(_config, "codegen_vision_model", "qwen-vl-max-latest")
        monkeypatch.setattr(_config, "llm_base_url", "https://text.example/v1")
        monkeypatch.setattr(_config, "llm_api_key", "text-key")
        monkeypatch.setattr(_config, "llm_model", "deepseek-v4")

        client = LLMClient.for_vision_codegen()
        assert client._model == "qwen-vl-max-latest"
        # The OpenAI client itself doesn't expose base_url cleanly, but a
        # smoke check via the inner client confirms we didn't fall through
        # to the text endpoint.
        assert client._model != "deepseek-v4"


class TestChatMultimodalMessageShape:
    def test_multimodal_user_content_includes_image_url_block(self, tmp_path):
        """chat_multimodal must build an OpenAI-style content array with
        one text block plus one image_url block per attached image."""
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        client, mock_create = _make_client(responses=["html generated"])
        with patch("reforge.config.config") as mock_config:
            mock_config.max_retry = 0
            client.chat_multimodal("sys", "user msg", [img])
        assert mock_create.call_count == 1
        messages = mock_create.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "sys"}
        user_content = messages[1]["content"]
        assert isinstance(user_content, list)
        assert user_content[0] == {"type": "text", "text": "user msg"}
        image_block = user_content[1]
        assert image_block["type"] == "image_url"
        assert image_block["image_url"]["url"].startswith("data:image/png;base64,")

    def test_multimodal_with_multiple_images_attaches_all(self, tmp_path):
        a = tmp_path / "a.png"
        b = tmp_path / "b.jpg"
        a.write_bytes(b"\x89PNG\r\n\x1a\nA")
        b.write_bytes(b"\xff\xd8\xff\xe0fakejpg")
        client, mock_create = _make_client(responses=["ok"])
        with patch("reforge.config.config") as mock_config:
            mock_config.max_retry = 0
            client.chat_multimodal("sys", "u", [a, b])
        user_content = mock_create.call_args.kwargs["messages"][1]["content"]
        image_blocks = [c for c in user_content if c.get("type") == "image_url"]
        assert len(image_blocks) == 2
        # Mime types preserved per file extension.
        assert image_blocks[0]["image_url"]["url"].startswith("data:image/png")
        assert image_blocks[1]["image_url"]["url"].startswith("data:image/jpeg")

    def test_multimodal_skips_missing_files(self, tmp_path):
        """A non-existent path must be skipped rather than crashing the
        whole call — the runtime may pass speculative paths."""
        real = tmp_path / "real.png"
        real.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        client, mock_create = _make_client(responses=["ok"])
        with patch("reforge.config.config") as mock_config:
            mock_config.max_retry = 0
            client.chat_multimodal(
                "sys", "u", [real, tmp_path / "missing.png"]
            )
        user_content = mock_create.call_args.kwargs["messages"][1]["content"]
        # 1 text + 1 image (the missing one is dropped, not faulted).
        assert sum(1 for c in user_content if c.get("type") == "image_url") == 1

    def test_multimodal_with_no_resolvable_images_falls_back_to_text(self, tmp_path):
        """If every image path is missing, send a plain text message so
        the caller still gets a response. Silent no-op would be worse."""
        client, mock_create = _make_client(responses=["text-only response"])
        with patch("reforge.config.config") as mock_config:
            mock_config.max_retry = 0
            result = client.chat_multimodal(
                "sys", "u", [tmp_path / "ghost.png"]
            )
        assert result == "text-only response"
        # The message should be a plain string (text-only path), not a list.
        user_content = mock_create.call_args.kwargs["messages"][1]["content"]
        assert user_content == "u"


class TestEncodeImageDataUrl:
    def test_png_extension_yields_png_mime(self, tmp_path):
        from reforge.models.adapters.llm_client import _encode_image_data_url

        img = tmp_path / "x.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        url = _encode_image_data_url(img)
        assert url.startswith("data:image/png;base64,")

    def test_unknown_extension_falls_back_to_png(self, tmp_path):
        from reforge.models.adapters.llm_client import _encode_image_data_url

        img = tmp_path / "noext"
        img.write_bytes(b"data")
        url = _encode_image_data_url(img)
        # Defensive default — let the vision API surface a 4xx if it
        # really can't handle the bytes, rather than crashing here.
        assert url.startswith("data:image/png;base64,")
