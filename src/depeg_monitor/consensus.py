"""Median consensus, quorum, numeraire scaling, and venue divergence.

A single bad feed should not page anyone. The median ignores one outlier,
and a quorum of sources and venue families is required before an alert.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from depeg_monitor.evaluate import classify
from depeg_monitor.models import GroupSpread, Observation, Quote

# Aggregators are useful fallbacks but are not part of the oracle-lag check.
_DIVERGENCE_CATEGORIES = ("chainlink", "dex", "cex")


def deviation_bps(price: float, peg: float) -> float:
    if peg <= 0:
        raise ValueError("peg must be positive")
    return abs(price - peg) / peg * 10_000


def direction_zh(price: float, peg: float, peg_label: str) -> str:
    if price < peg:
        return f"低于{peg_label}"
    if price > peg:
        return f"高于{peg_label}"
    return f"等于{peg_label}"


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median of empty list")
    return float(statistics.median(values))


def _sane(price: float, low: float, high: float) -> bool:
    return low <= price <= high and price > 0


def normalize_quotes(
    quotes: list[Quote],
    *,
    sanity: dict[str, tuple[float, float]],
    min_references: int = 2,
) -> list[Quote]:
    """Scale stablecoin-quoted prices by that stablecoin's USD median.

    References are quotes that are already in USD (Chainlink, USD CEX pairs,
    aggregators). Quotes being scaled are not used as their own reference, so
    a USDT-quoted USDC price cannot move the USDT reference it is scaled by.
    """
    refs: dict[str, list[float]] = defaultdict(list)
    for quote in quotes:
        if not quote.ok or quote.price is None or quote.quote_asset != "USD":
            continue
        band = sanity.get(quote.coin)
        if band is None or not _sane(quote.price, band[0], band[1]):
            continue
        refs[quote.coin].append(quote.price)
    factors = {
        coin: median(prices) for coin, prices in refs.items() if len(prices) >= min_references
    }

    scaled: list[Quote] = []
    for quote in quotes:
        asset = quote.quote_asset
        if (
            quote.ok
            and quote.price is not None
            and asset != "USD"
            and asset in factors
            and factors[asset] > 0
        ):
            factor = factors[asset]
            detail = quote.detail or ""
            note = f"scaled by {asset}/USD {factor:.6f}"
            scaled.append(
                Quote(
                    coin=quote.coin,
                    source=quote.source,
                    category=quote.category,
                    ok=True,
                    price=quote.price * factor,
                    quote_asset="USD",
                    detail=f"{detail}; {note}".strip("; "),
                    observed_at=quote.observed_at,
                )
            )
        else:
            scaled.append(quote)
    return scaled


def collapse_source_quotes(quotes: list[Quote]) -> list[Quote]:
    """Median quotes that came from the same module after numeraire scaling.

    A module may return one row per pool. Those rows are not independent
    venues, so they become a single consensus input. Rows that are still
    quoted in different assets are left unmerged.
    """
    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[Quote]] = defaultdict(list)
    for quote in quotes:
        key = (quote.coin, quote.source)
        if key not in grouped:
            order.append(key)
        grouped[key].append(quote)
    collapsed: list[Quote] = []
    for key in order:
        rows = grouped[key]
        ok = [row for row in rows if row.ok and row.price is not None]
        if not ok:
            collapsed.append(rows[0])
            continue
        assets = {row.quote_asset for row in ok}
        if len(assets) != 1:
            collapsed.extend(ok)
            continue
        asset = assets.pop()
        collapsed.append(
            Quote(
                coin=key[0],
                source=key[1],
                category=ok[0].category,
                ok=True,
                price=median([row.price for row in ok if row.price is not None]),
                quote_asset=asset,
                detail="; ".join(row.detail for row in ok if row.detail),
                observed_at=ok[0].observed_at,
            )
        )
    return collapsed


def group_spread(quotes: list[Quote]) -> GroupSpread:
    buckets: dict[str, list[float]] = defaultdict(list)
    for quote in quotes:
        if quote.category in _DIVERGENCE_CATEGORIES and quote.price is not None:
            buckets[quote.category].append(quote.price)
    medians = {name: median(prices) for name, prices in buckets.items() if prices}
    if len(medians) < 2:
        return GroupSpread(medians=medians, spread_bps=None)
    values = list(medians.values())
    mid = median(values)
    if mid <= 0:
        return GroupSpread(medians=medians, spread_bps=None)
    spread = (max(values) - min(values)) / mid * 10_000
    return GroupSpread(medians=medians, spread_bps=spread)


def observe(
    coin: str,
    quotes: list[Quote],
    *,
    peg: float | None,
    peg_label: str,
    sanity_min: float,
    sanity_max: float,
    min_sources: int,
    min_categories: int,
    info_bps: float,
    warning_bps: float,
    critical_bps: float,
) -> Observation:
    """Median the sane quotes and alert only when the quorum is met.

    ``deviation_bps`` is filled whenever a price and a peg both exist, so the
    cycle summary can show a move that is not allowed to page. ``level`` is
    set only when ``data_ok`` is true.
    """
    usable = [
        quote
        for quote in quotes
        if quote.ok and quote.price is not None and _sane(quote.price, sanity_min, sanity_max)
    ]
    categories = {quote.category for quote in usable}
    price = median([quote.price for quote in usable if quote.price is not None]) if usable else None
    groups = group_spread(usable)
    quorum = len(usable) >= min_sources and len(categories) >= min_categories
    if price is None:
        reason = "no usable prices"
        data_ok = False
    elif peg is None or peg <= 0:
        reason = "peg unavailable"
        data_ok = False
    elif not quorum:
        reason = (
            f"quorum {len(usable)}/{min_sources} sources, "
            f"{len(categories)}/{min_categories} categories"
        )
        data_ok = False
    else:
        reason = "ok"
        data_ok = True
    deviation = (
        deviation_bps(price, peg) if price is not None and peg is not None and peg > 0 else None
    )
    level = (
        classify(deviation, info=info_bps, warning=warning_bps, critical=critical_bps)
        if data_ok and deviation is not None
        else None
    )
    direction = (
        direction_zh(price, peg, peg_label)
        if price is not None and peg is not None and peg > 0
        else ""
    )
    return Observation(
        coin=coin,
        data_ok=data_ok,
        reason=reason,
        price=price,
        peg=peg if data_ok else peg,
        peg_label=peg_label,
        deviation_bps=deviation,
        level=level if data_ok else None,
        usable=len(usable),
        categories=len(categories),
        min_sources=min_sources,
        min_categories=min_categories,
        groups=groups,
        direction_zh=direction,
    )
