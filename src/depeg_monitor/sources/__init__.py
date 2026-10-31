"""Price source registry. Each module owns one venue and can fail on its own."""

from __future__ import annotations

from depeg_monitor.config import AppConfig
from depeg_monitor.sources.binance import BinanceSource
from depeg_monitor.sources.bybit import BybitSource
from depeg_monitor.sources.chainlink import ChainlinkSource
from depeg_monitor.sources.coinbase import CoinbaseSource
from depeg_monitor.sources.coingecko import CoinGeckoSource
from depeg_monitor.sources.curve import CurveSource
from depeg_monitor.sources.defillama import DefiLlamaSource
from depeg_monitor.sources.kraken import KrakenSource
from depeg_monitor.sources.okx import OkxSource
from depeg_monitor.sources.uniswap_v3 import UniswapV3Source


def build_sources(config: AppConfig) -> list[object]:
    sources: list[object] = []
    if config.chainlink_enabled:
        sources.append(ChainlinkSource(config))
    if config.curve_enabled:
        sources.append(CurveSource(config))
    if config.uniswap_enabled:
        sources.append(UniswapV3Source(config))
    for source_cls, key in (
        (BinanceSource, "binance"),
        (CoinbaseSource, "coinbase"),
        (KrakenSource, "kraken"),
        (OkxSource, "okx"),
        (BybitSource, "bybit"),
    ):
        if config.cex[key].enabled:
            sources.append(source_cls(config))
    if config.llama_enabled:
        sources.append(DefiLlamaSource(config))
    if config.gecko_enabled:
        sources.append(CoinGeckoSource(config))
    return sources
