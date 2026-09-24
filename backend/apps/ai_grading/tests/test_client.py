"""Klient SDK Anthropic: kształt wywołania ``beta.messages.stream`` i tłumaczenie błędów.

SDK ``anthropic`` jest tu **prawdziwe** tylko w części wyjątków – klasy błędów budujemy z jego
własnych konstruktorów, żeby łańcuch ``isinstance`` w ``client.translate_error`` był sprawdzany
na tym samym, na czym zadziała na produkcji. Sam klient jest podmieniony: żadne żądanie nie
wychodzi do sieci.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.ai_grading import client, crypto

from .conftest import FAKE_KEY

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx2")


# --- klient ---------------------------------------------------------------------------------------


class FakeStream:
    def __init__(self, message=None, error=None):
        self.message = message
        self.error = error

    def __enter__(self):
        if self.error is not None:
            raise self.error
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, message=None, error=None, model_info=None):
        self.calls: list[dict] = []
        self._stream = FakeStream(message, error)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._record))
        self.models = SimpleNamespace(retrieve=self._retrieve)
        self._model_info = model_info
        self._error = error

    def _record(self, **kwargs):
        self.calls.append(kwargs)
        return self._stream

    def _retrieve(self, model):
        if self._error is not None:
            raise self._error
        return self._model_info or SimpleNamespace(display_name="Claude Opus 5")


def message(**overrides):
    data = {
        "stop_reason": "end_turn",
        "stop_details": None,
        "model": "claude-opus-5",
        "content": [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text='{"a": 1}'),
        ],
        "usage": SimpleNamespace(
            input_tokens=10, output_tokens=20, cache_creation_input_tokens=30, cache_read_input_tokens=None
        ),
        "_request_id": "req_abc",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def status_error(cls, status: int, headers: dict | None = None):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, headers={"request-id": "req_err", **(headers or {})}, request=request)
    return cls("błąd", response=response, body={"error": {"type": "x"}})


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def factory(api_key, *, timeout, max_retries):
        holder["api_key"] = api_key
        holder["timeout"] = timeout
        holder["max_retries"] = max_retries
        return holder["client"]

    monkeypatch.setattr(client, "_client", factory)
    return holder


def test_call_model_streams_the_request_as_given_and_reads_the_final_message(fake, settings):
    settings.AI_GRADING_REQUEST_TIMEOUT = 123
    settings.AI_GRADING_SDK_MAX_RETRIES = 2
    fake["client"] = FakeClient(message())
    request = {"model": "claude-opus-5", "max_tokens": 32000, "messages": []}

    result = client.call_model(crypto.ApiKey(FAKE_KEY), request)

    assert fake["client"].calls == [request]
    assert fake["api_key"].reveal() == FAKE_KEY
    assert (fake["timeout"], fake["max_retries"]) == (123.0, 2)
    assert result.text == '{"a": 1}'
    assert result.request_id == "req_abc"
    assert result.usage == client.Usage(10, 20, 30, 0)


def test_refusal_category_is_read_from_stop_details(fake):
    fake["client"] = FakeClient(
        message(stop_reason="refusal", content=[], stop_details=SimpleNamespace(category="cyber"))
    )

    result = client.call_model(crypto.ApiKey(FAKE_KEY), {"model": "claude-opus-5"})

    assert result.stop_reason == "refusal"
    assert result.refusal_category == "cyber"
    assert result.text == ""


def test_real_sdk_client_gets_the_key_timeout_and_retries(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", Recorder)

    client._client(crypto.ApiKey(FAKE_KEY), timeout=60.0, max_retries=2)

    assert seen == {"api_key": FAKE_KEY, "timeout": 60.0, "max_retries": 2}


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (lambda: status_error(anthropic.AuthenticationError, 401), "auth", False),
        (lambda: status_error(anthropic.PermissionDeniedError, 403), "permission", False),
        (lambda: status_error(anthropic.NotFoundError, 404), "not_found", False),
        (lambda: status_error(anthropic.RateLimitError, 429, {"retry-after": "42"}), "rate_limit", True),
        (lambda: status_error(anthropic.InternalServerError, 500), "server", True),
        (lambda: status_error(anthropic.APIStatusError, 529), "server", True),
        (lambda: status_error(anthropic.BadRequestError, 400), "api", False),
        (
            lambda: anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            ),
            "connection",
            True,
        ),
    ],
)
def test_sdk_errors_are_translated_most_specific_first(fake, error, code, retryable):
    fake["client"] = FakeClient(error=error())

    with pytest.raises(client.ApiFailure) as info:
        client.call_model(crypto.ApiKey(FAKE_KEY), {"model": "claude-opus-5"})

    assert info.value.code == code
    assert info.value.retryable is retryable
    assert FAKE_KEY not in info.value.message
    if code == "rate_limit":
        assert info.value.retry_after == 42
    if code not in ("connection",):
        assert info.value.request_id == "req_err"


def test_failures_are_logged_with_the_request_id_but_never_the_key(fake, caplog):
    fake["client"] = FakeClient(error=status_error(anthropic.AuthenticationError, 401))

    with pytest.raises(client.ApiFailure):
        client.call_model(crypto.ApiKey(FAKE_KEY), {"model": "claude-opus-5"})

    assert "req_err" in caplog.text
    assert FAKE_KEY not in caplog.text


def test_check_key_uses_the_models_endpoint(fake):
    fake["client"] = FakeClient()

    ok, text = client.check_key(crypto.ApiKey(FAKE_KEY), "claude-opus-5")

    assert ok is True
    assert "Claude Opus 5" in text
    assert fake["max_retries"] == 0


def test_check_key_reports_a_rejected_key(fake):
    fake["client"] = FakeClient(error=status_error(anthropic.AuthenticationError, 401))

    ok, text = client.check_key(crypto.ApiKey(FAKE_KEY), "claude-opus-5")

    assert ok is False
    assert "odrzucił klucz" in text
