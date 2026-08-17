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
