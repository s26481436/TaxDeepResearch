"""Retry behaviour for transient LLM gateway failures.

The gateway fronting this deployment answers overload with 400 rather than 429,
and a batched extraction fires eight to ten calls in a row — so a single
transient refusal used to abort the whole run.
"""

from __future__ import annotations

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, BadRequestError, NotFoundError

from taxwatch.analysis.client import LLMClient


def _status_error(cls, status: int):
    request = httpx.Request("POST", "http://llm.local/v1/chat/completions")
    response = httpx.Response(status_code=status, request=request)
    return cls("boom", response=response, body=None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(LLMClient, "__init__", lambda self: None)
    c = LLMClient()
    c.model = "test-model"
    c.temperature = 0.1
    c.max_tokens = 1024
    c.retry_attempts = 4
    c.retry_base_delay = 0.0  # no real sleeping in tests
    c.retry_max_delay = 0.0
    c.retry_on_bad_request = True
    return c


def test_retries_bad_request_then_succeeds(client, monkeypatch):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _status_error(BadRequestError, 400)
        return "ok"

    client.client = type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": staticmethod(create)})()})()})()

    assert client._create_with_retry(messages=[]) == "ok"
    assert calls["n"] == 3


def test_gives_up_after_attempts_and_reraises(client):
    def create(**kwargs):
        raise _status_error(BadRequestError, 400)

    client.client = type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": staticmethod(create)})()})()})()

    with pytest.raises(BadRequestError):
        client._create_with_retry(messages=[])


def test_bad_request_not_retried_when_disabled(client):
    client.retry_on_bad_request = False
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise _status_error(BadRequestError, 400)

    client.client = type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": staticmethod(create)})()})()})()

    with pytest.raises(BadRequestError):
        client._create_with_retry(messages=[])
    # A genuinely malformed request must fail immediately, not after five waits.
    assert calls["n"] == 1


def test_permanent_error_is_not_retried(client):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise _status_error(NotFoundError, 404)

    client.client = type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": staticmethod(create)})()})()})()

    with pytest.raises(NotFoundError):
        client._create_with_retry(messages=[])
    assert calls["n"] == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(client, status):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise _status_error(BadRequestError, status)
        return "ok"

    client.client = type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": staticmethod(create)})()})()})()

    assert client._create_with_retry(messages=[]) == "ok"
    assert calls["n"] == 2


def test_connection_and_timeout_errors_are_retried(client):
    request = httpx.Request("POST", "http://llm.local/v1/chat/completions")
    for exc in (APIConnectionError(request=request), APITimeoutError(request=request)):
        calls = {"n": 0}

        def create(**kwargs, ):
            calls["n"] += 1
            if calls["n"] < 2:
                raise exc
            return "ok"

        client.client = type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": staticmethod(create)})()})()})()
        assert client._create_with_retry(messages=[]) == "ok"
        assert calls["n"] == 2


def test_sdk_level_retries_are_disabled(monkeypatch):
    """Our retry loop must be the only one, or backoffs stack invisibly."""
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("taxwatch.analysis.client.OpenAI", FakeOpenAI)
    LLMClient()

    assert captured["max_retries"] == 0


# ---------- WSO2 APIM endpoint suspension ----------


def _suspension_error(status: int = 400):
    request = httpx.Request("POST", "http://gw.local/v1/chat/completions")
    body = {
        "code": "303001",
        "message": "Message processing failed",
        "description": "Currently , Address endpoint : [ Name : ai-endpoint ] [ State : SUSPENDED ]",
    }
    response = httpx.Response(status_code=status, request=request, json=body)
    return BadRequestError("suspended", response=response, body=body)


def test_suspension_is_detected_from_body():
    from taxwatch.analysis.client import _is_suspension

    assert _is_suspension(_suspension_error())
    assert not _is_suspension(_status_error(BadRequestError, 400))


def test_backoff_never_returns_a_near_zero_wait(client):
    """A circuit breaker refuses instant retries; half of each delay is fixed."""
    client.retry_base_delay = 8.0
    client.retry_max_delay = 120.0

    for attempt in range(1, 5):
        delays = [client._backoff_delay(attempt, _suspension_error()) for _ in range(200)]
        capped = min(8.0 * (2 ** (attempt - 1)), 120.0)
        assert min(delays) >= capped / 2
        assert max(delays) <= capped


def test_backoff_is_capped(client):
    client.retry_base_delay = 5.0
    client.retry_max_delay = 60.0
    assert client._backoff_delay(20, _suspension_error()) <= 60.0


def test_retry_after_header_wins(client):
    request = httpx.Request("POST", "http://gw.local/v1/chat/completions")
    response = httpx.Response(429, request=request, headers={"Retry-After": "45"})
    exc = _status_error(BadRequestError, 429)
    exc.response = response

    client.retry_base_delay = 1.0
    client.retry_max_delay = 120.0
    assert client._backoff_delay(1, exc) == 45.0


def test_retry_after_is_still_capped(client):
    request = httpx.Request("POST", "http://gw.local/v1/chat/completions")
    response = httpx.Response(429, request=request, headers={"Retry-After": "99999"})
    exc = _status_error(BadRequestError, 429)
    exc.response = response

    client.retry_max_delay = 120.0
    assert client._backoff_delay(1, exc) == 120.0


def test_total_retry_window_outlasts_a_typical_suspension(client):
    """Six attempts at 5s base must span minutes, not seconds."""
    client.retry_attempts = 6
    client.retry_base_delay = 5.0
    client.retry_max_delay = 120.0

    worst = sum(
        min(5.0 * (2 ** (a - 1)), 120.0) / 2 for a in range(1, client.retry_attempts)
    )
    assert worst >= 60
