import pytest

from depeg_monitor.consensus import (
    collapse_source_quotes,
    deviation_bps,
    group_spread,
    normalize_quotes,
    observe,
)
from depeg_monitor.models import Quote


def q(coin, source, category, price, asset="USD", ok=True, error=None):
    return Quote(
        coin=coin,
        source=source,
        category=category,
        ok=ok,
        price=price,
        quote_asset=asset,
        error=error,
    )


def test_median_ignores_one_bad_feed():
    quotes = [
        q("USDC", "chainlink", "chainlink", 1.0),
        q("USDC", "curve", "dex", 1.001),
        q("USDC", "uniswap_v3", "dex", 0.999),
        q("USDC", "coinbase", "cex", 1.0),
        q("USDC", "binance", "cex", 0.55),
    ]
    obs = observe(
        "USDC",
        quotes,
        peg=1.0,
        peg_label="锚定",
        sanity_min=0.5,
        sanity_max=1.5,
        min_sources=3,
        min_categories=2,
        info_bps=20,
        warning_bps=50,
        critical_bps=100,
    )
    assert obs.data_ok
    assert obs.price == 1.0
    assert obs.level is None
    assert obs.deviation_bps < 20


def test_quorum_blocks_a_single_source():
    quotes = [q("USDC", "binance", "cex", 0.90)]
    obs = observe(
        "USDC",
        quotes,
        peg=1.0,
        peg_label="锚定",
        sanity_min=0.5,
        sanity_max=1.5,
        min_sources=3,
        min_categories=2,
        info_bps=20,
        warning_bps=50,
        critical_bps=100,
    )
    assert not obs.data_ok
    assert obs.level is None
    assert obs.price == 0.90
    assert obs.deviation_bps == deviation_bps(0.90, 1.0)


def test_category_quorum():
    quotes = [
        q("USDC", "binance", "cex", 0.90),
        q("USDC", "coinbase", "cex", 0.91),
        q("USDC", "kraken", "cex", 0.92),
    ]
    obs = observe(
        "USDC",
        quotes,
        peg=1.0,
        peg_label="锚定",
        sanity_min=0.5,
        sanity_max=1.5,
        min_sources=3,
        min_categories=2,
        info_bps=20,
        warning_bps=50,
        critical_bps=100,
    )
    assert not obs.data_ok
    assert "categories" in obs.reason


def test_sanity_band_drops_garbage_before_the_median():
    quotes = [
        q("USDC", "chainlink", "chainlink", 1.0),
        q("USDC", "curve", "dex", 1.0),
        q("USDC", "coinbase", "cex", 10.0),
    ]
    obs = observe(
        "USDC",
        quotes,
        peg=1.0,
        peg_label="锚定",
        sanity_min=0.5,
        sanity_max=1.5,
        min_sources=2,
        min_categories=2,
        info_bps=20,
        warning_bps=50,
        critical_bps=100,
    )
    assert obs.usable == 2
    assert obs.price == 1.0


def test_scale_usdt_quoted_price_by_usdt_usd_reference():
    quotes = [
        q("USDT", "chainlink", "chainlink", 0.999),
        q("USDT", "coinbase", "cex", 1.001),
        q("USDC", "binance", "cex", 1.002, asset="USDT"),
        q("USDC", "chainlink", "chainlink", 1.0),
    ]
    scaled = normalize_quotes(
        quotes,
        sanity={"USDT": (0.5, 1.5), "USDC": (0.5, 1.5)},
    )
    usdc = next(item for item in scaled if item.coin == "USDC" and item.source == "binance")
    assert usdc.quote_asset == "USD"
    assert usdc.price == pytest.approx(1.002)


def test_one_usd_reference_does_not_scale():
    quotes = [
        q("USDT", "chainlink", "chainlink", 0.99),
        q("USDC", "binance", "cex", 1.01, asset="USDT"),
    ]
    scaled = normalize_quotes(quotes, sanity={"USDT": (0.5, 1.5), "USDC": (0.5, 1.5)})
    usdc = next(item for item in scaled if item.coin == "USDC")
    assert usdc.quote_asset == "USDT"
    assert usdc.price == 1.01


def test_collapse_medians_one_venue():
    quotes = [
        q("USDC", "uniswap_v3", "dex", 1.002),
        q("USDC", "uniswap_v3", "dex", 0.998),
        q("USDC", "curve", "dex", 1.0),
    ]
    collapsed = collapse_source_quotes(quotes)
    assert len(collapsed) == 2
    uni = next(item for item in collapsed if item.source == "uniswap_v3")
    assert uni.price == pytest.approx(1.0)


def test_group_spread_is_relative():
    spread = group_spread(
        [
            q("sUSDe", "chainlink", "chainlink", 1.20),
            q("sUSDe", "curve", "dex", 1.26),
        ]
    )
    assert spread.spread_bps is not None
    mid = (1.20 + 1.26) / 2
    assert spread.spread_bps == pytest.approx((1.26 - 1.20) / mid * 10_000)
