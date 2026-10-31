"""Chainlink push feeds via ``latestRoundData`` on each configured chain.

Feeds for the same coin are reduced to one median quote so three chains do
not count as three independent sources.
"""

from __future__ import annotations

from collections import defaultdict

from depeg_monitor.config import AppConfig
from depeg_monitor.consensus import median
from depeg_monitor.evm import (
    SEL_LATEST_ROUND,
    AbiError,
    is_fresh,
    parse_latest_round_data,
    price_from_answer,
)
from depeg_monitor.http_util import short_err
from depeg_monitor.models import Quote
from depeg_monitor.rpc import RpcError
from depeg_monitor.session import FetchContext


def fresh_feed_price(data: str, decimals: int, now: float, max_age_seconds: int) -> float:
    answer, updated_at = parse_latest_round_data(data)
    if not is_fresh(updated_at, now, max_age_seconds):
        raise ValueError(f"stale updated_at={updated_at}")
    price = price_from_answer(answer, decimals)
    if price <= 0:
        raise ValueError("non-positive answer")
    return price


class ChainlinkSource:
    name = "chainlink"
    category = "chainlink"

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def fetch(self, ctx: FetchContext) -> list[Quote]:
        enabled = {coin.symbol for coin in self.config.enabled_coins()}
        grouped: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for coin, feeds in self.config.chainlink_feeds.items():
            if coin not in enabled:
                continue
            for index, feed in enumerate(feeds):
                grouped[feed.chain].append((f"{coin}:{index}", feed.address, feed.decimals))

        raw_by_id: dict[str, str | None] = {}
        chain_errors: dict[str, str] = {}
        for chain, items in grouped.items():
            calls = [(item_id, address, SEL_LATEST_ROUND) for item_id, address, _ in items]
            try:
                raw_by_id.update(ctx.rpc.eth_call_many(chain, calls))
            except RpcError as exc:
                chain_errors[chain] = short_err(exc)

        quotes: list[Quote] = []
        for coin, feeds in self.config.chainlink_feeds.items():
            if coin not in enabled:
                continue
            prices: list[float] = []
            notes: list[str] = []
            problems: list[str] = []
            for index, feed in enumerate(feeds):
                if feed.chain in chain_errors:
                    problems.append(f"{feed.chain}: {chain_errors[feed.chain]}")
                    continue
                payload = raw_by_id.get(f"{coin}:{index}")
                if not payload:
                    problems.append(f"{feed.chain}: empty")
                    continue
                try:
                    price = fresh_feed_price(
                        payload, feed.decimals, ctx.now, self.config.chainlink_max_age
                    )
                except (AbiError, ValueError) as exc:
                    problems.append(f"{feed.chain}: {exc}")
                    continue
                prices.append(price)
                notes.append(f"{feed.chain} {price:.6f}")
            if not prices:
                quotes.append(
                    Quote(
                        coin=coin,
                        source=self.name,
                        category="chainlink",
                        ok=False,
                        error="; ".join(problems) or "no feeds",
                    )
                )
                continue
            quotes.append(
                Quote(
                    coin=coin,
                    source=self.name,
                    category="chainlink",
                    ok=True,
                    price=median(prices),
                    quote_asset="USD",
                    detail=", ".join(notes),
                )
            )
        return quotes
