"""YAML configuration. Secrets stay in the environment, never in this file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SEVERITIES = ("info", "warning", "critical")


class ConfigError(ValueError):
    """The YAML config is missing a field or breaks a safety rule."""


@dataclass(frozen=True)
class Thresholds:
    info: float
    warning: float
    critical: float
    recovery: float
    cooldown: dict[str, int]


@dataclass(frozen=True)
class TokenRef:
    chain: str
    address: str
    decimals: int


@dataclass(frozen=True)
class VaultSpec:
    chain: str
    address: str
    one_share: int
    rate_decimals: int
    numeraire: str


@dataclass(frozen=True)
class CoinSpec:
    symbol: str
    enabled: bool
    peg_mode: str
    peg: float
    thresholds: Thresholds
    sanity_min: float
    sanity_max: float
    min_sources: int
    min_categories: int
    token: TokenRef | None
    vault: VaultSpec | None


@dataclass(frozen=True)
class FeedSpec:
    chain: str
    address: str
    decimals: int


@dataclass(frozen=True)
class CurvePoolSpec:
    coin: str
    chain: str
    address: str
    i: int
    j: int
    dx_decimals: int
    quote_decimals: int
    quote_asset: str
    label: str


@dataclass(frozen=True)
class UniPoolSpec:
    coin: str
    chain: str
    address: str
    base_is_token0: bool
    decimals0: int
    decimals1: int
    quote_asset: str
    label: str


@dataclass(frozen=True)
class CexSymbol:
    symbol: str
    quote: str


@dataclass(frozen=True)
class CexSpec:
    enabled: bool
    base_url: str
    symbols: dict[str, CexSymbol]


@dataclass(frozen=True)
class BalancePool:
    name: str
    chain: str
    address: str
    coins: list[str]
    decimals: list[int]


@dataclass(frozen=True)
class Heuristics:
    imbalance_excess_pp: float
    imbalance_jump_pp: float
    supply_change_pct: float
    supply_window_seconds: int
    depth_drop_pct: float
    funding_stress_rate: float
    funding_coins: frozenset[str]
    funding_instruments: list[str]
    pools: list[BalancePool]


@dataclass(frozen=True)
class AppConfig:
    coins: list[CoinSpec]
    chains: dict[str, list[str]]
    divergence_bps: float
    chainlink_enabled: bool
    chainlink_max_age: int
    chainlink_feeds: dict[str, list[FeedSpec]]
    curve_enabled: bool
    curve_api_base: str
    curve_api_max_age: int
    curve_pools: list[CurvePoolSpec]
    uniswap_enabled: bool
    uniswap_pools: list[UniPoolSpec]
    cex: dict[str, CexSpec]
    llama_enabled: bool
    llama_base: str
    llama_max_age: int
    gecko_enabled: bool
    gecko_base: str
    gecko_ids: dict[str, str]
    heuristics: Heuristics
    source_enabled: dict[str, bool] = field(default_factory=dict)

    def coin(self, symbol: str) -> CoinSpec | None:
        for item in self.coins:
            if item.symbol == symbol:
                return item
        return None

    def enabled_coins(self) -> list[CoinSpec]:
        return [coin for coin in self.coins if coin.enabled]

    def sanity_bands(self) -> dict[str, tuple[float, float]]:
        return {coin.symbol: (coin.sanity_min, coin.sanity_max) for coin in self.coins}


def load_config(path: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    defaults = _mapping(raw.get("defaults") or {}, "defaults")
    chains = _chains(_mapping(raw.get("chains") or {}, "chains"))
    coins = _coins(raw.get("coins"), defaults, chains)
    sources = _mapping(raw.get("sources") or {}, "sources")
    heuristics = _heuristics(_mapping(raw.get("context") or {}, "context"), chains, coins)
    chainlink = _chainlink(sources.get("chainlink") or {}, chains, coins)
    curve = _curve(sources.get("curve") or {}, chains, coins)
    uniswap = _uniswap(sources.get("uniswap_v3") or {}, chains, coins)
    cex = {
        name: _cex(name, sources.get(name) or {}, coins)
        for name in ("binance", "coinbase", "kraken", "okx", "bybit")
    }
    llama = _mapping(sources.get("defillama") or {}, "sources.defillama")
    gecko = _mapping(sources.get("coingecko") or {}, "sources.coingecko")
    gecko_ids = _str_map(gecko.get("ids") or {}, "sources.coingecko.ids")
    for symbol, coin in ((c.symbol, c) for c in coins):
        if coin.peg_mode == "vault":
            assert coin.vault is not None
            if coin.vault.numeraire == symbol or all(
                other.symbol != coin.vault.numeraire for other in coins
            ):
                raise ConfigError(f"{symbol} vault numeraire must be another configured coin")
    divergence = _float(defaults.get("divergence_bps", 40), "defaults.divergence_bps")
    if divergence <= 0:
        raise ConfigError("divergence_bps must be positive")
    return AppConfig(
        coins=coins,
        chains=chains,
        divergence_bps=divergence,
        chainlink_enabled=bool(chainlink["enabled"]),
        chainlink_max_age=_pos_int(chainlink["max_age"], "chainlink.max_age_seconds"),
        chainlink_feeds=chainlink["feeds"],
        curve_enabled=bool(curve["enabled"]),
        curve_api_base=str(curve["api_base"]).rstrip("/"),
        curve_api_max_age=_pos_int(curve["api_max_age"], "curve.api_max_age_seconds"),
        curve_pools=curve["pools"],
        uniswap_enabled=bool(uniswap["enabled"]),
        uniswap_pools=uniswap["pools"],
        cex=cex,
        llama_enabled=bool(llama.get("enabled", True)),
        llama_base=str(llama.get("base_url", "https://coins.llama.fi")).rstrip("/"),
        llama_max_age=_pos_int(llama.get("max_age_seconds", 21600), "defillama.max_age_seconds"),
        gecko_enabled=bool(gecko.get("enabled", True)),
        gecko_base=str(gecko.get("base_url", "https://api.coingecko.com")).rstrip("/"),
        gecko_ids=gecko_ids,
        heuristics=heuristics,
    )


def rpc_urls(config: AppConfig, chain: str) -> list[str]:
    """Public defaults, with ``RPC_<CHAIN>_URL`` prepended (comma-separated)."""
    env_key = f"RPC_{chain.upper()}_URL"
    extra = [part.strip() for part in os.environ.get(env_key, "").split(",") if part.strip()]
    return extra + list(config.chains.get(chain, []))


def _coins(
    raw: object, defaults: dict[str, object], chains: dict[str, list[str]]
) -> list[CoinSpec]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("coins must be a non-empty mapping")
    default_thresholds = _thresholds(defaults, "defaults")
    coins: list[CoinSpec] = []
    for symbol, body in raw.items():
        if not isinstance(symbol, str) or not symbol:
            raise ConfigError("coin symbol must be a string")
        item = _mapping(body or {}, f"coins.{symbol}")
        peg_mode = str(item.get("peg_mode", defaults.get("peg_mode", "usd")))
        if peg_mode not in ("usd", "vault"):
            raise ConfigError(f"{symbol} peg_mode must be usd or vault")
        thresholds = _thresholds(item, f"coins.{symbol}", fallback=default_thresholds)
        token = (
            _token(item.get("token"), f"coins.{symbol}.token", chains)
            if item.get("token")
            else None
        )
        vault = None
        if peg_mode == "vault":
            vault = _vault(
                _mapping(item.get("vault") or {}, f"coins.{symbol}.vault"), symbol, chains
            )
        sanity_min = _float(
            item.get("sanity_min", defaults.get("sanity_min", 0.5)), f"{symbol}.sanity_min"
        )
        sanity_max = _float(
            item.get("sanity_max", defaults.get("sanity_max", 1.5)), f"{symbol}.sanity_max"
        )
        if not 0 < sanity_min < sanity_max:
            raise ConfigError(f"{symbol} sanity band is invalid")
        min_sources = int(item.get("min_sources", defaults.get("min_sources", 3)))
        min_categories = int(item.get("min_categories", defaults.get("min_categories", 2)))
        if min_sources < 1 or not 1 <= min_categories <= 4:
            raise ConfigError(f"{symbol} quorum is invalid")
        coins.append(
            CoinSpec(
                symbol=symbol,
                enabled=bool(item.get("enabled", True)),
                peg_mode=peg_mode,
                peg=_float(item.get("peg", defaults.get("peg", 1.0)), f"{symbol}.peg"),
                thresholds=thresholds,
                sanity_min=sanity_min,
                sanity_max=sanity_max,
                min_sources=min_sources,
                min_categories=min_categories,
                token=token,
                vault=vault,
            )
        )
    return coins


def _thresholds(
    raw: dict[str, object],
    where: str,
    fallback: Thresholds | None = None,
) -> Thresholds:
    block = raw.get("thresholds_bps", None)
    if block is None and fallback is not None:
        info, warning, critical = fallback.info, fallback.warning, fallback.critical
    else:
        levels = _mapping(block or {}, f"{where}.thresholds_bps")
        info = _float(levels.get("info", fallback.info if fallback else None), f"{where}.info")
        warning = _float(
            levels.get("warning", fallback.warning if fallback else None), f"{where}.warning"
        )
        critical = _float(
            levels.get("critical", fallback.critical if fallback else None), f"{where}.critical"
        )
    if not 0 < info < warning < critical:
        raise ConfigError(f"{where} thresholds must rise info < warning < critical")
    recovery = _float(
        raw.get("recovery_bps", fallback.recovery if fallback else None), f"{where}.recovery_bps"
    )
    if not 0 < recovery < info:
        raise ConfigError(f"{where} recovery_bps must be positive and below the info tier")
    cooldown_raw = raw.get("cooldown_seconds", None)
    if cooldown_raw is None and fallback is not None:
        cooldown = dict(fallback.cooldown)
    else:
        cooldown_map = _mapping(cooldown_raw or {}, f"{where}.cooldown_seconds")
        cooldown = {}
        for name in _SEVERITIES:
            fallback_value = fallback.cooldown[name] if fallback else None
            cooldown[name] = _pos_int(
                cooldown_map.get(name, fallback_value), f"{where}.cooldown_seconds.{name}"
            )
    return Thresholds(
        info=info, warning=warning, critical=critical, recovery=recovery, cooldown=cooldown
    )


def _chains(raw: dict[str, object]) -> dict[str, list[str]]:
    chains: dict[str, list[str]] = {}
    for name, urls in raw.items():
        if not isinstance(urls, list) or not urls:
            raise ConfigError(f"chains.{name} needs at least one URL")
        chains[str(name)] = [str(url) for url in urls]
    if "ethereum" not in chains:
        raise ConfigError("chains.ethereum is required")
    return chains


def _chainlink(
    raw: object, chains: dict[str, list[str]], coins: list[CoinSpec]
) -> dict[str, object]:
    body = _mapping(raw or {}, "sources.chainlink")
    feeds_raw = _mapping(body.get("feeds") or {}, "sources.chainlink.feeds")
    known = {coin.symbol for coin in coins}
    feeds: dict[str, list[FeedSpec]] = {}
    for symbol, items in feeds_raw.items():
        if symbol not in known:
            raise ConfigError(f"chainlink feed for unknown coin {symbol}")
        if not isinstance(items, list) or not items:
            raise ConfigError(f"chainlink.feeds.{symbol} must be a list")
        parsed: list[FeedSpec] = []
        for index, item in enumerate(items):
            feed = _mapping(item, f"chainlink.feeds.{symbol}[{index}]")
            chain = str(feed.get("chain", "ethereum"))
            _require_chain(chain, chains, f"chainlink.feeds.{symbol}")
            parsed.append(
                FeedSpec(
                    chain=chain,
                    address=_address(feed.get("address"), f"chainlink.feeds.{symbol}"),
                    decimals=_pos_int(feed.get("decimals"), f"chainlink.feeds.{symbol}.decimals"),
                )
            )
        feeds[str(symbol)] = parsed
    return {
        "enabled": bool(body.get("enabled", True)),
        "max_age": body.get("max_age_seconds", 93600),
        "feeds": feeds,
    }


def _curve(raw: object, chains: dict[str, list[str]], coins: list[CoinSpec]) -> dict[str, object]:
    body = _mapping(raw or {}, "sources.curve")
    known = {coin.symbol for coin in coins}
    pools: list[CurvePoolSpec] = []
    for index, item in enumerate(body.get("pools") or []):
        pool = _mapping(item, f"curve.pools[{index}]")
        coin = str(pool.get("coin"))
        if coin not in known:
            raise ConfigError(f"curve pool coin {coin} is not configured")
        chain = str(pool.get("chain", "ethereum"))
        _require_chain(chain, chains, f"curve.pools[{index}]")
        pools.append(
            CurvePoolSpec(
                coin=coin,
                chain=chain,
                address=_address(pool.get("address"), f"curve.pools[{index}]"),
                i=_nonneg_int(pool.get("i"), "curve i"),
                j=_nonneg_int(pool.get("j"), "curve j"),
                dx_decimals=_nonneg_int(pool.get("dx_decimals"), "curve dx_decimals"),
                quote_decimals=_nonneg_int(pool.get("quote_decimals"), "curve quote_decimals"),
                quote_asset=str(pool.get("quote", "USD")),
                label=str(pool.get("label", coin)),
            )
        )
    return {
        "enabled": bool(body.get("enabled", True)),
        "api_base": body.get("api_base", "https://prices.curve.fi"),
        "api_max_age": body.get("api_max_age_seconds", 43200),
        "pools": pools,
    }


def _uniswap(raw: object, chains: dict[str, list[str]], coins: list[CoinSpec]) -> dict[str, object]:
    body = _mapping(raw or {}, "sources.uniswap_v3")
    known = {coin.symbol for coin in coins}
    pools: list[UniPoolSpec] = []
    for index, item in enumerate(body.get("pools") or []):
        pool = _mapping(item, f"uniswap_v3.pools[{index}]")
        coin = str(pool.get("coin"))
        if coin not in known:
            raise ConfigError(f"uniswap pool coin {coin} is not configured")
        chain = str(pool.get("chain", "ethereum"))
        _require_chain(chain, chains, f"uniswap_v3.pools[{index}]")
        pools.append(
            UniPoolSpec(
                coin=coin,
                chain=chain,
                address=_address(pool.get("address"), f"uniswap_v3.pools[{index}]"),
                base_is_token0=bool(pool.get("base_is_token0", True)),
                decimals0=_nonneg_int(pool.get("decimals0"), "decimals0"),
                decimals1=_nonneg_int(pool.get("decimals1"), "decimals1"),
                quote_asset=str(pool.get("quote", "USD")),
                label=str(pool.get("label", coin)),
            )
        )
    return {"enabled": bool(body.get("enabled", True)), "pools": pools}


def _cex(name: str, raw: object, coins: list[CoinSpec]) -> CexSpec:
    body = _mapping(raw or {}, f"sources.{name}")
    known = {coin.symbol for coin in coins}
    symbols: dict[str, CexSymbol] = {}
    raw_symbols = body.get("symbols") or {}
    if not isinstance(raw_symbols, dict):
        raise ConfigError(f"sources.{name}.symbols must be a mapping")
    for coin, spec in raw_symbols.items():
        if coin not in known:
            raise ConfigError(f"{name} symbol for unknown coin {coin}")
        item = _mapping(spec, f"sources.{name}.symbols.{coin}")
        symbol = str(item.get("symbol") or "")
        quote = str(item.get("quote") or "USD")
        if not symbol:
            raise ConfigError(f"{name} symbol for {coin} is empty")
        symbols[str(coin)] = CexSymbol(symbol=symbol, quote=quote)
    return CexSpec(
        enabled=bool(body.get("enabled", True)),
        base_url=str(body.get("base_url", "")).rstrip("/"),
        symbols=symbols,
    )


def _heuristics(
    raw: dict[str, object], chains: dict[str, list[str]], coins: list[CoinSpec]
) -> Heuristics:
    pools: list[BalancePool] = []
    for index, item in enumerate(raw.get("pools") or []):
        pool = _mapping(item, f"context.pools[{index}]")
        names = pool.get("coins")
        decimals = pool.get("decimals")
        if (
            not isinstance(names, list)
            or not isinstance(decimals, list)
            or len(names) != len(decimals)
        ):
            raise ConfigError(f"context.pools[{index}] coins and decimals must match")
        chain = str(pool.get("chain", "ethereum"))
        _require_chain(chain, chains, f"context.pools[{index}]")
        pools.append(
            BalancePool(
                name=str(pool.get("name", f"pool{index}")),
                chain=chain,
                address=_address(pool.get("address"), f"context.pools[{index}]"),
                coins=[str(name) for name in names],
                decimals=[_nonneg_int(dec, "decimals") for dec in decimals],
            )
        )
    funding_coins = raw.get("funding_coins") or ["USDe", "sUSDe"]
    if not isinstance(funding_coins, list):
        raise ConfigError("context.funding_coins must be a list")
    instruments = raw.get("funding_instruments") or ["ETH-USDT-SWAP", "BTC-USDT-SWAP"]
    if not isinstance(instruments, list):
        raise ConfigError("context.funding_instruments must be a list")
    known = {coin.symbol for coin in coins}
    return Heuristics(
        imbalance_excess_pp=_float(raw.get("imbalance_excess_pp", 20), "imbalance_excess_pp"),
        imbalance_jump_pp=_float(raw.get("imbalance_jump_pp", 5), "imbalance_jump_pp"),
        supply_change_pct=_float(raw.get("supply_change_pct", 3), "supply_change_pct"),
        supply_window_seconds=_pos_int(
            raw.get("supply_window_seconds", 86400), "supply_window_seconds"
        ),
        depth_drop_pct=_float(raw.get("depth_drop_pct", 20), "depth_drop_pct"),
        funding_stress_rate=_float(raw.get("funding_stress_rate", -0.0002), "funding_stress_rate"),
        funding_coins=frozenset(str(item) for item in funding_coins if str(item) in known),
        funding_instruments=[str(item) for item in instruments],
        pools=pools,
    )


def _token(raw: object, where: str, chains: dict[str, list[str]]) -> TokenRef:
    body = _mapping(raw, where)
    chain = str(body.get("chain", "ethereum"))
    _require_chain(chain, chains, where)
    return TokenRef(
        chain=chain,
        address=_address(body.get("address"), where),
        decimals=_nonneg_int(body.get("decimals"), f"{where}.decimals"),
    )


def _vault(raw: dict[str, object], symbol: str, chains: dict[str, list[str]]) -> VaultSpec:
    chain = str(raw.get("chain", "ethereum"))
    _require_chain(chain, chains, f"{symbol}.vault")
    return VaultSpec(
        chain=chain,
        address=_address(raw.get("address"), f"{symbol}.vault"),
        one_share=_pos_int(raw.get("one_share"), f"{symbol}.vault.one_share"),
        rate_decimals=_nonneg_int(raw.get("rate_decimals"), f"{symbol}.vault.rate_decimals"),
        numeraire=str(raw.get("numeraire") or ""),
    )


def _require_chain(chain: str, chains: dict[str, list[str]], where: str) -> None:
    if chain not in chains:
        raise ConfigError(f"{where} references unknown chain {chain}")


def _address(value: object, where: str) -> str:
    text = str(value or "")
    if not _ADDRESS.match(text):
        raise ConfigError(f"{where} address is invalid: {text!r}")
    return text


def _mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping")
    return value


def _str_map(value: object, where: str) -> dict[str, str]:
    body = _mapping(value, where)
    return {str(key): str(item) for key, item in body.items()}


def _float(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{where} must be a number")
    return float(value)


def _pos_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{where} must be a positive integer (do not use 1e18 notation)")
    return value


def _nonneg_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{where} must be a non-negative integer")
    return value
