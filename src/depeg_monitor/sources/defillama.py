"""DeFiLlama current coin prices, used as an aggregator fallback."""

from __future__ import annotations

import httpx

from depeg_monitor.config import AppConfig
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import Quote
from depeg_monitor.session import FetchContext


def parse_llama_price(payload: object, address: str) -> tuple[float, int | None, float | None]:
    if not isinstance(payload, dict):
        raise ValueError("defillama payload is not an object")
    coins = payload.get("coins")
    if not isinstance(coins, dict):
        raise ValueError("defillama coins missing")
    target = None
    needle = address.lower()
    for key, value in coins.items():
        if str(key).lower().endswith(needle):
            target = value
            break
    if not isinstance(target, dict) or "price" not in target:
        raise ValueError("token missing from defillama")
    price = float(target["price"])
    if price <= 0:
        raise ValueError("defillama price is non-positive")
    timestamp = target.get("timestamp")
    confidence = target.get("confidence")
    return (
        price,
        int(timestamp) if isinstance(timestamp, int | float) else None,
        float(confidence) if isinstance(confidence, int | float) else None,
    )


class DefiLlamaSource:
    name = "defillama"
    category = "aggregator"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        if not self.config.llama_enabled:
            return []
        coins = [coin for coin in self.config.enabled_coins() if coin.token is not None]
        if not coins:
            return []
        keys = ",".join(f"{coin.token.chain}:{coin.token.address}" for coin in coins if coin.token)
        url = f"{self.config.llama_base}/prices/current/{keys}"
        try:
            payload = request_json(ctx.client, url)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return [
                Quote(
                    coin=coin.symbol,
                    source=self.name,
                    category="aggregator",
                    ok=False,
                    error=short_err(exc),
                )
                for coin in coins
            ]
        quotes: list[Quote] = []
        for coin in coins:
            assert coin.token is not None
            try:
                price, timestamp, confidence = parse_llama_price(payload, coin.token.address)
            except (ValueError, TypeError) as exc:
                quotes.append(
                    Quote(
                        coin=coin.symbol,
                        source=self.name,
                        category="aggregator",
                        ok=False,
                        error=short_err(exc),
                    )
                )
                continue
            if timestamp is not None and ctx.now - timestamp > self.config.llama_max_age:
                quotes.append(
                    Quote(
                        coin=coin.symbol,
                        source=self.name,
                        category="aggregator",
                        ok=False,
                        error=f"stale ({int(ctx.now - timestamp)}s)",
                    )
                )
                continue
            if confidence is not None and confidence < 0.5:
                quotes.append(
                    Quote(
                        coin=coin.symbol,
                        source=self.name,
                        category="aggregator",
                        ok=False,
                        error=f"low confidence {confidence:.2f}",
                    )
                )
                continue
            quotes.append(
                Quote(
                    coin=coin.symbol,
                    source=self.name,
                    category="aggregator",
                    ok=True,
                    price=price,
                    quote_asset="USD",
                    observed_at=float(timestamp) if timestamp is not None else None,
                    detail=None if confidence is None else f"confidence {confidence:.2f}",
                )
            )
        return quotes
