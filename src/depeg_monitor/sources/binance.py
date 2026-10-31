"""Binance spot tickers from the public market-data host.

``api.binance.com`` returns HTTP 451 from many cloud regions, including
GitHub-hosted runners. ``data-api.binance.vision`` serves the same public
ticker payload without that block. Prices are USDT-quoted unless configured
otherwise, and the consensus step scales them by a USDT/USD reference.
"""

from __future__ import annotations

import json

import httpx

from depeg_monitor.config import AppConfig
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import Quote
from depeg_monitor.session import FetchContext


def parse_binance_prices(payload: object) -> dict[str, float]:
    rows = payload if isinstance(payload, list) else [payload]
    parsed: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict) or "symbol" not in row or "price" not in row:
            continue
        price = float(row["price"])
        if price > 0:
            parsed[str(row["symbol"])] = price
    if not parsed:
        message = ""
        if isinstance(payload, dict):
            message = str(payload.get("msg") or "")
        raise ValueError(message or "no positive binance prices")
    return parsed


class BinanceSource:
    name = "binance"
    category = "cex"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        spec = self.config.cex["binance"]
        if not spec.enabled:
            return []
        wanted = {
            item.symbol: (coin, item.quote)
            for coin, item in spec.symbols.items()
            if any(enabled.symbol == coin for enabled in self.config.enabled_coins())
        }
        if not wanted:
            return []
        prices, errors = _load_prices(ctx, spec.base_url, list(wanted))
        quotes: list[Quote] = []
        for symbol, (coin, quote_asset) in wanted.items():
            price = prices.get(symbol)
            if price is None:
                quotes.append(
                    Quote(
                        coin=coin,
                        source=self.name,
                        category="cex",
                        ok=False,
                        error=errors.get(symbol, f"{symbol} missing"),
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
                    quote_asset=quote_asset,
                    detail=symbol,
                )
            )
        return quotes


def _load_prices(
    ctx: FetchContext, base_url: str, symbols: list[str]
) -> tuple[dict[str, float], dict[str, str]]:
    """Prefer one batched ticker request, then fall back per symbol."""
    url = f"{base_url}/api/v3/ticker/price"
    batch = json.dumps(symbols, separators=(",", ":"))
    try:
        payload = request_json(ctx.client, url, params={"symbols": batch})
        return parse_binance_prices(payload), {}
    except (httpx.HTTPError, ValueError, TypeError):
        prices: dict[str, float] = {}
        errors: dict[str, str] = {}
        for symbol in symbols:
            try:
                payload = request_json(ctx.client, url, params={"symbol": symbol})
                prices.update(parse_binance_prices(payload))
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                errors[symbol] = short_err(exc)
        return prices, errors
