from datetime import UTC, datetime

import httpx
import pytest

from depeg_monitor.evm import encode_get_dy, price_from_sqrt
from depeg_monitor.http_util import short_err
from depeg_monitor.sources.binance import parse_binance_prices
from depeg_monitor.sources.bybit import parse_bybit_price
from depeg_monitor.sources.chainlink import fresh_feed_price
from depeg_monitor.sources.coinbase import parse_coinbase_price
from depeg_monitor.sources.coingecko import parse_gecko_prices
from depeg_monitor.sources.curve import parse_curve_api_price, price_from_get_dy
from depeg_monitor.sources.defillama import parse_llama_price
from depeg_monitor.sources.kraken import parse_kraken_price
from depeg_monitor.sources.okx import parse_okx_funding, parse_okx_ticker
from depeg_monitor.sources.uniswap_v3 import price_from_slot0

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def test_http_errors_keep_only_the_status_code():
    request = httpx.Request("GET", "https://api.bybit.com/v5/market/tickers")
    response = httpx.Response(403, request=request)
    error = httpx.HTTPStatusError("nope", request=request, response=response)
    assert short_err(error) == "HTTP 403"


def _round(answer: int, updated: int) -> str:
    words = [1, answer % 2**256, updated, updated, 1]
    return "0x" + "".join(f"{word:064x}" for word in words)


def _word(value: int) -> str:
    return "0x" + f"{value:064x}"


def test_chainlink_price_and_staleness():
    payload = _round(99_976_000, 1_700_000_000)
    assert fresh_feed_price(payload, 8, 1_700_000_100, 3_600) == pytest.approx(0.99976)
    with pytest.raises(ValueError, match="stale"):
        fresh_feed_price(payload, 8, 1_700_004_000, 3_600)
    with pytest.raises(ValueError, match="non-positive"):
        fresh_feed_price(_round(-5, 1_700_000_000), 8, 1_700_000_010, 3_600)


def test_curve_get_dy_and_api_age():
    assert price_from_get_dy(_word(1_000_827), 6) == pytest.approx(1.000827)
    with pytest.raises(ValueError):
        price_from_get_dy(_word(0), 6)
    assert encode_get_dy(1, 2, 10**6).startswith("0x5e0d443f")
    fresh_at = datetime(2026, 10, 31, 8, 0, tzinfo=UTC).timestamp()
    fresh = {"data": {"usd_price": 0.9993, "last_updated": "2026-10-31T08:00:00"}}
    assert parse_curve_api_price(fresh, now=fresh_at + 60, max_age_seconds=43_200) == pytest.approx(
        0.9993
    )
    stale = {"data": {"usd_price": 0.99, "last_updated": "2026-10-29T03:05:47Z"}}
    with pytest.raises(ValueError, match="stale"):
        parse_curve_api_price(stale, now=fresh_at, max_age_seconds=43_200)


def test_uniswap_slot0_decimal_adjustment():
    one = _word(2**96) + "00" * 32
    assert price_from_slot0(one, base_is_token0=True, decimals0=6, decimals1=6) == pytest.approx(1)
    # token1/token0 raw = 1, so a 18-decimal token0 vs 6-decimal token1 is 1e12 human.
    assert price_from_slot0(one, base_is_token0=True, decimals0=18, decimals1=6) == pytest.approx(
        1e12
    )
    assert price_from_sqrt(2**96, base_is_token0=False, decimals0=6, decimals1=6) == pytest.approx(
        1
    )


def test_binance_parser_skips_zero_and_errors():
    assert parse_binance_prices(
        [{"symbol": "USDCUSDT", "price": "1.00124000"}, {"symbol": "DAIUSDT", "price": "0"}]
    ) == {"USDCUSDT": pytest.approx(1.00124)}
    with pytest.raises(ValueError):
        parse_binance_prices({"code": 0, "msg": "restricted"})


def test_coinbase_kraken_okx_bybit_parsers():
    assert parse_coinbase_price({"data": {"amount": "0.998925", "base": "USDT"}}) == pytest.approx(
        0.998925
    )
    with pytest.raises(ValueError):
        parse_coinbase_price({"message": "not found"})
    kraken = {"error": [], "result": {"USDTZUSD": {"c": ["0.99889000", "1"]}}}
    assert parse_kraken_price(kraken) == pytest.approx(0.99889)
    with pytest.raises(ValueError, match="Unknown"):
        parse_kraken_price({"error": ["EQuery:Unknown asset pair"], "result": {}})
    assert parse_okx_ticker({"code": "0", "data": [{"last": "1.00086"}]}) == pytest.approx(1.00086)
    assert parse_okx_funding(
        {"code": "0", "data": [{"fundingRate": "-0.00008778"}]}
    ) == pytest.approx(-0.00008778)
    with pytest.raises(ValueError):
        parse_okx_ticker({"code": "51001", "data": [], "msg": "missing"})
    bybit = {"retCode": 0, "result": {"list": [{"lastPrice": "0.9998"}]}}
    assert parse_bybit_price(bybit) == pytest.approx(0.9998)
    with pytest.raises(ValueError, match="empty"):
        parse_bybit_price({"retCode": 0, "result": {"list": []}})


def test_defillama_and_coingecko_parsers():
    payload = {
        "coins": {
            f"ethereum:{USDC}": {"price": 0.9999, "timestamp": 100, "confidence": 0.99},
        }
    }
    price, stamp, confidence = parse_llama_price(payload, USDC.lower())
    assert price == pytest.approx(0.9999)
    assert stamp == 100
    assert confidence == pytest.approx(0.99)
    with pytest.raises(ValueError):
        parse_llama_price({"coins": {}}, USDC)
    assert parse_gecko_prices({"usd-coin": {"usd": 0.999937}, "status": {}})[
        "usd-coin"
    ] == pytest.approx(0.999937)
    with pytest.raises(ValueError, match="slow"):
        parse_gecko_prices({"status": {"error_code": 429, "error_message": "slow down"}})
