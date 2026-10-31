"""JSON-RPC ``eth_call`` client with a fallback list of public endpoints.

Calls are read-only view calls. The client has no transaction broadcast method.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import httpx

log = logging.getLogger(__name__)


class RpcError(RuntimeError):
    """Every configured endpoint failed for this batch."""


class RpcClient:
    def __init__(
        self, client: httpx.Client, endpoints: dict[str, list[str]], chunk_size: int = 20
    ) -> None:
        self.client = client
        self.endpoints = endpoints
        self.chunk_size = chunk_size
        self._lock = threading.Lock()

    def eth_call_many(self, chain: str, calls: list[tuple[str, str, str]]) -> dict[str, str | None]:
        """Run ``eth_call`` for ``(id, to, data)`` triples. Missing results are None."""
        urls = [url for url in self.endpoints.get(chain, []) if url]
        if not urls:
            raise RpcError(f"no RPC URL for {chain}")
        merged: dict[str, str | None] = {}
        for start in range(0, len(calls), self.chunk_size):
            chunk = calls[start : start + self.chunk_size]
            merged.update(self._chunk(chain, urls, chunk))
        return merged

    def _chunk(
        self, chain: str, urls: list[str], calls: list[tuple[str, str, str]]
    ) -> dict[str, str | None]:
        payload: list[dict[str, Any]] = [
            {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "eth_call",
                "params": [{"to": to, "data": data}, "latest"],
            }
            for call_id, to, data in calls
        ]
        last_error = "no attempt"
        # One batch at a time keeps public RPCs from seeing a burst as abuse.
        with self._lock:
            for url in urls:
                try:
                    response = self.client.post(url, json=payload)
                    response.raise_for_status()
                    body = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = f"{url}: {exc.__class__.__name__}"
                    log.debug("rpc %s failed on %s: %s", chain, url, exc.__class__.__name__)
                    continue
                parsed = _parse_batch(body)
                if parsed is None:
                    last_error = f"{url}: unexpected JSON-RPC payload"
                    continue
                return parsed
        raise RpcError(f"{chain} RPC failed ({last_error})")


def _parse_batch(body: object) -> dict[str, str | None] | None:
    if isinstance(body, dict):
        if body.get("error"):
            return None
        body = [body]
    if not isinstance(body, list):
        return None
    parsed: dict[str, str | None] = {}
    for item in body:
        if not isinstance(item, dict) or "id" not in item:
            continue
        result = item.get("result")
        parsed[str(item["id"])] = result if isinstance(result, str) else None
    return parsed
