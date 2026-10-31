"""Kraken public ticker. The result key is not always the requested pair name."""

from __future__ import annotations

import httpx

from depeg_monitor.config import AppConfig
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import Quote
from depeg_monitor.session import FetchContext


def parse_kraken_price(payload: object) -> float:
    if not isinstance(payload, dict):
        raise ValueError("kraken payload is not an object")
    errors = payload.get("error") or []
    if errors:
        raise ValueError("; ".join(str(item) for item in errors))
    result = payload.get("result")
    if not isinstance(result, dict) or not result:
        raise ValueError("kraken result is empty")
    first = next(iter(result.values()))
    if not isinstance(first, dict) or not first.get("c"):
        raise ValueError("kraken last trade missing")
    price = float(first["c"][0])
    if price <= 0:
        raise ValueError("kraken price is non-positive")
    return price


class KrakenSource:
    name = "kraken"
    category = "cex"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        spec = self.config.cex["kraken"]
        quotes: list[Quote] = []
        if not spec.enabled:
            return quotes
        enabled = {coin.symbol for coin in self.config.enabled_coins()}
        for coin, item in spec.symbols.items():
            if coin not in enabled:
                continue
            url = f"{spec.base_url}/0/public/Ticker"
            try:
                price = parse_kraken_price(
                    request_json(ctx.client, url, params={"pair": item.symbol})
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
