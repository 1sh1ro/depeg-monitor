"""Bybit spot tickers.

Bybit's public API often answers HTTP 403 to cloud provider IP ranges. The
source records that failure and the rest of the monitor continues.
"""

from __future__ import annotations

import httpx

from depeg_monitor.config import AppConfig
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import Quote
from depeg_monitor.session import FetchContext


def parse_bybit_price(payload: object) -> float:
    if not isinstance(payload, dict):
        raise ValueError("bybit payload is not an object")
    if payload.get("retCode") not in (0, "0"):
        raise ValueError(str(payload.get("retMsg") or "bybit error"))
    result = payload.get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ValueError("bybit ticker list is empty")
    price = float(rows[0]["lastPrice"])
    if price <= 0:
        raise ValueError("bybit price is non-positive")
    return price


class BybitSource:
    name = "bybit"
    category = "cex"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        spec = self.config.cex["bybit"]
        quotes: list[Quote] = []
        if not spec.enabled:
            return quotes
        enabled = {coin.symbol for coin in self.config.enabled_coins()}
        for coin, item in spec.symbols.items():
            if coin not in enabled:
                continue
            url = f"{spec.base_url}/v5/market/tickers"
            try:
                price = parse_bybit_price(
                    request_json(
                        ctx.client,
                        url,
                        params={"category": "spot", "symbol": item.symbol},
                    )
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                quotes.append(
                    Quote(
                        coin=coin, source=self.name, category="cex", ok=False, error=short_err(exc)
                    )
                )
                continue
            quotes.append(
                Quote(
                    coin=coin,
                    source=self.name,
                    category="cex",
                    ok=True,
                    price=price,
                    quote_asset=item.quote,
                    detail=item.symbol,
                )
            )
        return quotes
