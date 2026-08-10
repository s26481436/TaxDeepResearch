from __future__ import annotations

import time

import httpx

_DEFAULT_HEADERS = {
    "User-Agent": "TaxWatch/0.1 (tax-law-change-detection; contact: taxwatch@example.com)",
    "Accept": "text/html,application/xml,application/json",
}

_RETRY_DELAYS = [1, 2, 4]


def create_client(
    timeout: int = 30,
    headers: dict | None = None,
) -> httpx.Client:
    merged = {**_DEFAULT_HEADERS, **(headers or {})}
    return httpx.Client(timeout=timeout, headers=merged, follow_redirects=True)


def fetch_with_retry(
    client: httpx.Client,
    url: str,
    *,
    params: dict | None = None,
    max_retries: int = 3,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                time.sleep(delay)
    raise last_exc  # type: ignore[misc]
