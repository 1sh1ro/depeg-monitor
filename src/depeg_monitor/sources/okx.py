"""OKX public spot tickers and perpetual funding rates.

Funding is a context signal for hedge-backed stables such as USDe. It is not
a price, and a funding-rate failure does not remove OKX spot quotes.
"""

from __future__ import annotations

import httpx

from depeg_monitor.config import AppConfig
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import Quote
from depeg_monitor.session import FetchContext


def parse_okx_ticker(payload: object) -> float:
    if not isinstance(payload, dict):
        raise ValueError("okx payload is not an object")
    if str(payload.get("code")) != "0" or not payload.get("data"):
        raise ValueError(str(payload.get("msg") or "okx ticker error"))
    data = payload["data"]
    if not isinstance(data, list) or not isinstance(data[0], dict) or "last" not in data[0]:
        raise ValueError("okx last price missing")
    price = float(data[0]["last"])
    if price <= 0:
        raise ValueError("okx price is non-positive")
    return price


def parse_okx_funding(payload: object) -> float:
    """Return the current funding rate as a fraction per funding interval."""
    if not isinstance(payload, dict):
        raise ValueError("okx funding payload is not an object")
    if str(payload.get("code")) != "0" or not payload.get("data"):
        raise ValueError(str(payload.get("msg") or "okx funding error"))
    data = payload["data"]
    if not isinstance(data, list) or not isinstance(data[0], dict):
        raise ValueError("okx funding row missing")
    return float(data[0]["fundingRate"])


class OkxSource:
    name = "okx"
    category = "cex"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        spec = self.config.cex["okx"]
        quotes: list[Quote] = []
        if not spec.enabled:
            return quotes
        enabled = {coin.symbol for coin in self.config.enabled_coins()}
        for coin, item in spec.symbols.items():
            if coin not in enabled:
                continue
            url = f"{spec.base_url}/api/v5/market/ticker"
            try:
                price = parse_okx_ticker(
                    request_json(ctx.client, url, params={"instId": item.symbol})
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
