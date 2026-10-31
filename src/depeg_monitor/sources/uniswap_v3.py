"""Uniswap V3 spot prices from ``slot0``, not a router quote.

``slot0`` is the current pool price. It does not simulate a swap fee the way
QuoterV2 does; for stablecoin pools the fee is 1 bp or less, which is inside
the noise of a depeg threshold. Pool addresses are configured explicitly so
the monitor does not have to discover them.
"""

from __future__ import annotations

from collections import defaultdict

from depeg_monitor.config import AppConfig, UniPoolSpec
from depeg_monitor.evm import SEL_SLOT0, AbiError, price_from_sqrt, sqrt_price_x96
from depeg_monitor.http_util import short_err
from depeg_monitor.models import Quote
from depeg_monitor.rpc import RpcError
from depeg_monitor.session import FetchContext


def price_from_slot0(
    data: str,
    *,
    base_is_token0: bool,
    decimals0: int,
    decimals1: int,
) -> float:
    return price_from_sqrt(
        sqrt_price_x96(data),
        base_is_token0=base_is_token0,
        decimals0=decimals0,
        decimals1=decimals1,
    )


class UniswapV3Source:
    name = "uniswap_v3"
    category = "dex"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        enabled = {coin.symbol for coin in self.config.enabled_coins()}
        pools = [pool for pool in self.config.uniswap_pools if pool.coin in enabled]
        grouped: dict[str, list[tuple[int, UniPoolSpec]]] = defaultdict(list)
        for index, pool in enumerate(pools):
            grouped[pool.chain].append((index, pool))
        raw: dict[str, str | None] = {}
        chain_errors: dict[str, str] = {}
        for chain, items in grouped.items():
            calls = [(str(index), pool.address, SEL_SLOT0) for index, pool in items]
            try:
                raw.update(ctx.rpc.eth_call_many(chain, calls))
            except RpcError as exc:
                chain_errors[chain] = short_err(exc)

        # One quote per pool. The runner medians a coin's pools after scaling
        # quote assets into USD, so two pools are still one venue.
        quotes: list[Quote] = []
        for index, pool in enumerate(pools):
            if pool.chain in chain_errors:
                quotes.append(_failed(pool.coin, f"{pool.label}: {chain_errors[pool.chain]}"))
                continue
            payload = raw.get(str(index))
            if not payload:
                quotes.append(_failed(pool.coin, f"{pool.label}: empty"))
                continue
            try:
                price = price_from_slot0(
                    payload,
                    base_is_token0=pool.base_is_token0,
                    decimals0=pool.decimals0,
                    decimals1=pool.decimals1,
                )
            except (AbiError, ValueError) as exc:
                quotes.append(_failed(pool.coin, f"{pool.label}: {exc}"))
                continue
            if price <= 0:
                quotes.append(_failed(pool.coin, f"{pool.label}: non-positive"))
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
        return quotes


def _failed(coin: str, error: str) -> Quote:
    return Quote(coin=coin, source="uniswap_v3", category="dex", ok=False, error=error)
