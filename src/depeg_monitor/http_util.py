"""Small HTTP helper. Responses are untrusted data, never instructions."""

from __future__ import annotations

import time
from typing import Any

import httpx

# Geo-blocks and missing markets will not start working on a second try.
_NO_RETRY = {400, 401, 403, 404, 409, 422, 451}


def short_err(exc: BaseException) -> str:
    """One line, safe to put in an alert. HTTP errors stay as a status code."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    text = " ".join(str(exc).split())
    return (text or exc.__class__.__name__)[:200]


def request_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET JSON, retrying once on transport errors and transient HTTP statuses."""
    last: Exception | None = None
    for attempt in range(2):
        try:
            response = client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            last = exc
            if attempt == 0:
                time.sleep(0.3)
                continue
            raise
        if response.status_code in _NO_RETRY:
            response.raise_for_status()
        if response.status_code >= 500 or response.status_code == 429:
            last = httpx.HTTPStatusError(
                f"HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
            if attempt == 0:
                time.sleep(0.4)
                continue
            raise last
        response.raise_for_status()
        return response.json()
    assert last is not None
    raise last
