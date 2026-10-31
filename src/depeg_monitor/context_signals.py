"""Background signals that sit next to a price alert.

Supply, Curve pool balance, the sUSDe vault rate, and perpetual funding are
read from public endpoints. Any one of them can fail without suppressing the
price alert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from depeg_monitor.evm import (
    SEL_CONVERT_TO_ASSETS,
    SEL_TOTAL_SUPPLY,
    AbiError,
    encode_word_call,
    parse_uint256,
)
from depeg_monitor.http_util import request_json, short_err
from depeg_monitor.models import MonitorState
from depeg_monitor.rpc import RpcError
from depeg_monitor.session import FetchContext
from depeg_monitor.sources.okx import parse_okx_funding
from depeg_monitor.state import preview_supply_change


@dataclass
class PoolSnapshot:
    name: str
    shares: dict[str, float]
    excess_pp: dict[str, float]
    jump_pp: dict[str, float | None]
    depth: float
    depth_drop_pct: float | None


@dataclass
class ContextBundle:
    supply: dict[str, float] = field(default_factory=dict)
    supply_change: dict[str, tuple[float, float] | None] = field(default_factory=dict)
    pools: list[PoolSnapshot] = field(default_factory=list)
    vault_rates: dict[str, float] = field(default_factory=dict)
    vault_numeraires: dict[str, str] = field(default_factory=dict)
    funding: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


def collect_context(ctx: FetchContext, state: MonitorState) -> ContextBundle:
    bundle = ContextBundle()
    _read_chain(ctx, state, bundle)
    _read_funding(ctx, bundle)
    return bundle


def coin_signals(
    bundle: ContextBundle,
    coin: str,
    *,
    funding_coins: frozenset[str],
) -> tuple[list[str], dict[str, dict[str, object]]]:
    """Chinese lines for the alert, plus the numbers the rationale uses."""
    lines: list[str] = []
    values: dict[str, dict[str, object]] = {}
    if coin in bundle.supply:
        amount = bundle.supply[coin]
        change = bundle.supply_change.get(coin)
        if change is None:
            lines.append(f"供应约 {_fmt_amount(amount)}，还没有足够的历史样本可比。")
            values["supply"] = {"change_pct": None, "hours": None}
        else:
            percent, hours = change
            direction = "增加" if percent > 0 else "减少" if percent < 0 else "持平"
            lines.append(
                f"供应约 {_fmt_amount(amount)}，较 {hours:.1f} 小时前{direction} {percent:+.2f}%。"
                "增加通常对应净铸造，减少通常对应净赎回。"
            )
            values["supply"] = {"change_pct": percent, "hours": hours}
    elif any(item.startswith(f"supply {coin}") for item in bundle.failures):
        lines.append("供应：本轮读取失败，已忽略。")

    relevant = [pool for pool in bundle.pools if coin in pool.shares]
    if relevant:
        best_excess: float | None = None
        best_jump: float | None = None
        drops: list[float] = []
        for pool in relevant:
            share_text = " / ".join(
                f"{name} {pool.shares[name] * 100:.1f}%" for name in pool.shares
            )
            excess = pool.excess_pp.get(coin, 0.0)
            jump = pool.jump_pp.get(coin)
            jump_text = "，尚无上次快照" if jump is None else f"，较上次 {jump:+.1f} 个百分点"
            if excess >= 0:
                relation = f"高出均衡 {excess:.1f} 个百分点"
            else:
                relation = f"低于均衡 {abs(excess):.1f} 个百分点"
            lines.append(
                f"{pool.name} 占比 {share_text}（该币{relation}{jump_text}）。"
                f"池内代币存量约 {_fmt_amount(pool.depth)}。"
            )
            if best_excess is None or excess > best_excess:
                best_excess = excess
                best_jump = jump
            if pool.depth_drop_pct is not None:
                drops.append(pool.depth_drop_pct)
        values["pool"] = {
            "max_excess_pp": best_excess,
            "max_jump_pp": best_jump,
            "max_depth_drop_pct": max(drops) if drops else None,
        }
    elif any(item.startswith("pool ") for item in bundle.failures):
        lines.append("Curve 池占比：本轮有池子读取失败，已忽略失败的池子。")

    if coin in funding_coins:
        if bundle.funding:
            parts = [f"{name} {_bps(rate):+.2f} bps" for name, rate in bundle.funding.items()]
            lines.append("OKX 永续资金费（正数=多头付给空头）：" + "，".join(parts) + "。")
            values["funding"] = {"rates": list(bundle.funding.values())}
        else:
            lines.append("永续资金费：本轮没有取到。")

    if coin in bundle.vault_rates:
        numeraire = bundle.vault_numeraires.get(coin, "USDe")
        lines.append(f"金库汇率 {bundle.vault_rates[coin]:.6f} {numeraire}/股。")
    return lines, values


def _read_chain(ctx: FetchContext, state: MonitorState, bundle: ContextBundle) -> None:
    calls: dict[str, list[tuple[str, str, str]]] = {}
    for coin in ctx.config.enabled_coins():
        if coin.token is None:
            continue
        calls.setdefault(coin.token.chain, []).append(
            (f"supply:{coin.symbol}", coin.token.address, SEL_TOTAL_SUPPLY)
        )
        if coin.vault is not None:
            calls.setdefault(coin.vault.chain, []).append(
                (
                    f"vault:{coin.symbol}",
                    coin.vault.address,
                    encode_word_call(SEL_CONVERT_TO_ASSETS, coin.vault.one_share),
                )
            )
    for pool in ctx.config.heuristics.pools:
        for index in range(len(pool.coins)):
            calls.setdefault(pool.chain, []).append(
                (
                    f"bal:{pool.name}:{index}",
                    pool.address,
                    encode_word_call("0x4903b0d1", index),
                )
            )
    raw: dict[str, str | None] = {}
    for chain, items in calls.items():
        try:
            raw.update(ctx.rpc.eth_call_many(chain, items))
        except RpcError as exc:
            bundle.failures.append(f"rpc {chain}: {short_err(exc)}")

    for coin in ctx.config.enabled_coins():
        if coin.token is None:
            continue
        payload = raw.get(f"supply:{coin.symbol}")
        if not payload:
            bundle.failures.append(f"supply {coin.symbol}")
            continue
        try:
            human = parse_uint256(payload) / 10**coin.token.decimals
        except (AbiError, ValueError):
            bundle.failures.append(f"supply {coin.symbol}")
            continue
        bundle.supply[coin.symbol] = human
        bundle.supply_change[coin.symbol] = preview_supply_change(
            state.supply.get(coin.symbol, []),
            human,
            ctx.now,
            ctx.config.heuristics.supply_window_seconds,
        )
        if coin.vault is not None:
            vault_raw = raw.get(f"vault:{coin.symbol}")
            if not vault_raw:
                bundle.failures.append(f"vault {coin.symbol}")
            else:
                try:
                    rate = parse_uint256(vault_raw) / 10**coin.vault.rate_decimals
                except (AbiError, ValueError):
                    bundle.failures.append(f"vault {coin.symbol}")
                else:
                    if rate > 0:
                        bundle.vault_rates[coin.symbol] = rate
                        bundle.vault_numeraires[coin.symbol] = coin.vault.numeraire

    for pool in ctx.config.heuristics.pools:
        try:
            humans = []
            for index, decimals in enumerate(pool.decimals):
                payload = raw.get(f"bal:{pool.name}:{index}")
                if not payload:
                    raise ValueError(f"balance {index} empty")
                humans.append(parse_uint256(payload) / 10**decimals)
        except (AbiError, ValueError) as exc:
            bundle.failures.append(f"pool {pool.name}: {exc}")
            continue
        total = sum(humans)
        if total <= 0 or len(humans) != len(pool.coins):
            bundle.failures.append(f"pool {pool.name}: empty")
            continue
        shares = {name: amount / total for name, amount in zip(pool.coins, humans, strict=True)}
        equal = 100 / len(pool.coins)
        excess = {name: share * 100 - equal for name, share in shares.items()}
        previous = state.pool_excess_pp.get(pool.name, {})
        jump = {
            name: (value - previous[name] if name in previous else None)
            for name, value in excess.items()
        }
        prev_depth = state.pool_depth.get(pool.name)
        drop = ((prev_depth - total) / prev_depth * 100) if prev_depth and prev_depth > 0 else None
        bundle.pools.append(
            PoolSnapshot(
                name=pool.name,
                shares=shares,
                excess_pp=excess,
                jump_pp=jump,
                depth=total,
                depth_drop_pct=drop,
            )
        )


def _read_funding(ctx: FetchContext, bundle: ContextBundle) -> None:
    instruments = ctx.config.heuristics.funding_instruments
    if not instruments:
        return
    base = ctx.config.cex["okx"].base_url or "https://www.okx.com"
    for instrument in instruments:
        url = f"{base}/api/v5/public/funding-rate"
        try:
            payload = request_json(ctx.client, url, params={"instId": instrument})
            bundle.funding[instrument] = parse_okx_funding(payload)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            bundle.failures.append(f"funding {instrument}: {short_err(exc)}")


def _fmt_amount(value: float) -> str:
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿"
    if value >= 1e4:
        return f"{value / 1e4:.2f} 万"
    return f"{value:.2f}"


def _bps(rate: float) -> float:
    return rate * 10_000
