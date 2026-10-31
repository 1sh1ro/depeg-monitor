"""CoinGecko simple price, used as an aggregator fallback.

A demo API key is optional (``COINGECKO_API_KEY``). The free unauthenticated
endpoint is enough for this monitor and is allowed to fail.
"""

from __future__ import annotations

import os

import httpx

from depeg_monitor.config import AppConfig
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import Quote
from depeg_monitor.session import FetchContext


def parse_gecko_prices(payload: object) -> dict[str, float]:
    if not isinstance(payload, dict):
        raise ValueError("coingecko payload is not an object")
    status = payload.get("status")
    if isinstance(status, dict) and status.get("error_code"):
        raise ValueError(str(status.get("error_message") or status.get("error_code")))
    parsed: dict[str, float] = {}
    for coin_id, row in payload.items():
        if coin_id == "status" or not isinstance(row, dict) or "usd" not in row:
            continue
        price = float(row["usd"])
        if price > 0:
            parsed[str(coin_id)] = price
    if not parsed:
        raise ValueError("coingecko returned no usd prices")
    return parsed


class CoinGeckoSource:
    name = "coingecko"
    category = "aggregator"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        if not self.config.gecko_enabled:
            return []
        enabled = {coin.symbol for coin in self.config.enabled_coins()}
        ids = {
            coin: gecko_id for coin, gecko_id in self.config.gecko_ids.items() if coin in enabled
        }
        if not ids:
            return []
        headers = {}
        api_key = os.environ.get("COINGECKO_API_KEY", "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        url = f"{self.config.gecko_base}/api/v3/simple/price"
        try:
            payload = request_json(
                ctx.client,
                url,
                params={"ids": ",".join(ids.values()), "vs_currencies": "usd"},
                headers=headers or None,
            )
            prices = parse_gecko_prices(payload)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return [
                Quote(
                    coin=coin,
                    source=self.name,
                    category="aggregator",
                    ok=False,
                    error=short_err(exc),
                )
                for coin in ids
            ]
        quotes: list[Quote] = []
        for coin, gecko_id in ids.items():
            price = prices.get(gecko_id)
            if price is None:
                quotes.append(
                    Quote(
                        coin=coin,
                        source=self.name,
                        category="aggregator",
                        ok=False,
                        error=f"{gecko_id} missing",
                    )
                )
                continue
            quotes.append(
                Quote(
                    coin=coin,
                    source=self.name,
                    category="aggregator",
                    ok=True,
                    price=price,
                    quote_asset="USD",
                    detail=gecko_id,
                )
            )
        return quotes
