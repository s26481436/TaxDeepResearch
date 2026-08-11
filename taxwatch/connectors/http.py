from __future__ import annotations

import re
import time

import httpx

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml,application/json,*/*;q=0.9",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_RETRY_DELAYS = [1, 2, 4]

# chinatax.gov.cn intermittently answers with a tiny JS page instead of the real
# document: it sets a `C3VK` cookie via document.cookie and reloads itself. We
# don't run JS, so replay it by lifting the cookie value and re-requesting.
_CHALLENGE_MAX_BYTES = 2000
_CHALLENGE_COOKIE_RE = re.compile(r"C3VK=([0-9a-f]+)")


def create_client(
    timeout: int = 30,
    headers: dict | None = None,
) -> httpx.Client:
    merged = {**_DEFAULT_HEADERS, **(headers or {})}
    return httpx.Client(timeout=timeout, headers=merged, follow_redirects=True)


def _solve_cookie_challenge(client: httpx.Client, resp: httpx.Response) -> bool:
    """Set the challenge cookie on `client`. Returns True if a retry is warranted."""
    if len(resp.content) > _CHALLENGE_MAX_BYTES:
        return False
    match = _CHALLENGE_COOKIE_RE.search(resp.text)
    if not match:
        return False
    client.cookies.set("C3VK", match.group(1), domain=resp.url.host)
    return True


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
            if _solve_cookie_challenge(client, resp):
                resp = client.get(url, params=params)
                resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                time.sleep(delay)
    raise last_exc  # type: ignore[misc]
