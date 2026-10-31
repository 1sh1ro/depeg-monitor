"""Curve pool spot prices from on-chain ``get_dy``, with the Curve HTTP API as fallback.

``get_dy(i, j, 1 coin)`` is the spot a trader would receive right now. That is
a pool price, not a TWAP. The HTTP API is used only when no pool call succeeds,
and a stale API row is rejected.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

import httpx

from depeg_monitor.config import AppConfig, CoinSpec, CurvePoolSpec
from depeg_monitor.evm import AbiError, encode_get_dy, parse_uint256
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import Quote
from depeg_monitor.rpc import RpcError
from depeg_monitor.session import FetchContext


def price_from_get_dy(data: str, quote_decimals: int) -> float:
    """Human quote tokens out for an input of exactly one base token."""
    amount = parse_uint256(data)
    if amount <= 0:
        raise ValueError("get_dy returned 0")
    if quote_decimals < 0:
        raise ValueError("bad quote decimals")
    return amount / 10**quote_decimals


def parse_curve_api_price(payload: object, *, now: float, max_age_seconds: int) -> float:
    if not isinstance(payload, dict):
        raise ValueError("curve api payload is not an object")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("usd_price") is None:
        raise ValueError("curve api usd_price missing")
    price = float(data["usd_price"])
    if price <= 0:
        raise ValueError("curve api price is non-positive")
    updated = data.get("last_updated")
    if isinstance(updated, str) and updated:
        stamp = _parse_time(updated)
        age = now - stamp
        if age > max_age_seconds:
            raise ValueError(f"curve api price is stale ({int(age)}s)")
    return price


def _parse_time(value: str) -> float:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif "+" not in text[10:] and "-" not in text[10:]:
        text += "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


class CurveSource:
    name = "curve"
    category = "dex"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        enabled = {coin.symbol: coin for coin in self.config.enabled_coins()}
        quotes, problems = self._onchain(ctx, enabled)
        priced = {quote.coin for quote in quotes}
        for symbol, coin in enabled.items():
            if symbol in priced:
                continue
            if symbol not in problems and coin.token is None:
                continue
            fallback = self._api(ctx, coin)
            if fallback is not None and fallback.ok:
                quotes.append(fallback)
                continue
            parts: list[str] = []
            if symbol in problems:
                parts.append(problems[symbol])
            if fallback is not None and fallback.error:
                parts.append(fallback.error)
            if parts:
                quotes.append(
                    Quote(
                        coin=symbol,
                        source=self.name,
                        category="dex",
                        ok=False,
                        error="; ".join(parts),
                    )
                )
        return quotes

    def _onchain(
        self, ctx: FetchContext, enabled: dict[str, CoinSpec]
    ) -> tuple[list[Quote], dict[str, str]]:
        grouped: dict[str, list[tuple[str, CurvePoolSpec]]] = defaultdict(list)
        for index, pool in enumerate(self.config.curve_pools):
            if pool.coin not in enabled:
                continue
            grouped[pool.chain].append((f"{pool.coin}:{index}", pool))
        raw: dict[str, str | None] = {}
        chain_errors: dict[str, str] = {}
        for chain, items in grouped.items():
            calls = [
                (call_id, pool.address, encode_get_dy(pool.i, pool.j, 10**pool.dx_decimals))
                for call_id, pool in items
            ]
            try:
                raw.update(ctx.rpc.eth_call_many(chain, calls))
            except RpcError as exc:
                chain_errors[chain] = short_err(exc)
        quotes: list[Quote] = []
        problems: dict[str, str] = {}
        for index, pool in enumerate(self.config.curve_pools):
            if pool.coin not in enabled:
                continue
            if pool.chain in chain_errors:
                problems.setdefault(pool.coin, chain_errors[pool.chain])
                continue
            payload = raw.get(f"{pool.coin}:{index}")
            if not payload:
                problems.setdefault(pool.coin, f"{pool.label}: empty")
                continue
            try:
                price = price_from_get_dy(payload, pool.quote_decimals)
            except (AbiError, ValueError) as exc:
                problems.setdefault(pool.coin, f"{pool.label}: {exc}")
                continue
            quotes.append(
                Quote(
                    coin=pool.coin,
                    source=self.name,
                    category="dex",
                    ok=True,
                    price=price,
                    quote_asset=pool.quote_asset,
                    detail=f"{pool.label} {price:.6f} {pool.quote_asset}",
                )
            )
        return quotes, problems

    def _api(self, ctx: FetchContext, coin: CoinSpec) -> Quote | None:
        if coin.token is None:
            return None
        url = (
            f"{self.config.curve_api_base}/v1/usd_price/" f"{coin.token.chain}/{coin.token.address}"
        )
        try:
            payload = request_json(ctx.client, url)
            price = parse_curve_api_price(
                payload, now=ctx.now, max_age_seconds=self.config.curve_api_max_age
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return Quote(
                coin=coin.symbol,
                source=self.name,
                category="dex",
                ok=False,
                error=f"curve api: {short_err(exc)}",
            )
        return Quote(
            coin=coin.symbol,
            source=self.name,
            category="dex",
            ok=True,
            price=price,
            quote_asset="USD",
            detail="prices.curve.fi",
        )
