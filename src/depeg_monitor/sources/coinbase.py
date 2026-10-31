"""Coinbase spot prices. The public v2 price endpoint is already in USD."""

from __future__ import annotations

import httpx

from depeg_monitor.config import AppConfig
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import Quote
from depeg_monitor.session import FetchContext


def parse_coinbase_price(payload: object) -> float:
    if not isinstance(payload, dict):
        raise ValueError("coinbase payload is not an object")
    data = payload.get("data")
    if not isinstance(data, dict) or "amount" not in data:
        message = ""
        error = payload.get("message") or payload.get("error")
        if error:
            message = str(error)
        raise ValueError(message or "coinbase amount missing")
    price = float(data["amount"])
    if price <= 0:
        raise ValueError("coinbase price is non-positive")
    return price


class CoinbaseSource:
    name = "coinbase"
    category = "cex"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        spec = self.config.cex["coinbase"]
        quotes: list[Quote] = []
        if not spec.enabled:
            return quotes
        enabled = {coin.symbol for coin in self.config.enabled_coins()}
        for coin, item in spec.symbols.items():
            if coin not in enabled:
                continue
            url = f"{spec.base_url}/v2/prices/{item.symbol}/spot"
            try:
                price = parse_coinbase_price(request_json(ctx.client, url))
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
