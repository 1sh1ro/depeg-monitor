"""One monitor cycle: fetch, consensus, dedup, notify.

The cycle never holds a key and never builds a transaction. A source or a
context signal that fails is skipped. Missing data does not clear an open
alert, so a dead RPC is not reported as a recovery.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx

from depeg_monitor import __version__
from depeg_monitor.config import AppConfig, CoinSpec
from depeg_monitor.consensus import collapse_source_quotes, median, normalize_quotes, observe
from depeg_monitor.context_signals import ContextBundle, coin_signals, collect_context
from depeg_monitor.evaluate import decide
from depeg_monitor.formatting import (
    format_depeg,
    format_divergence,
    format_recovery,
    format_summary,
    summary_block,
)
from depeg_monitor.http_util import short_err
from depeg_monitor.models import Episode, MonitorState, Observation, Quote
from depeg_monitor.notify import deliver
from depeg_monitor.rationale import build_rationale
from depeg_monitor.rpc import RpcClient
from depeg_monitor.session import FetchContext
from depeg_monitor.sources import build_sources
from depeg_monitor.state import append_supply, save_state

log = logging.getLogger(__name__)


@dataclass
class _Outbound:
    text: str
    keys: list[str]


def run_once(
    config: AppConfig,
    state: MonitorState,
    *,
    dry_run: bool,
    save: bool,
    state_path: Path,
    notifiers: list[object],
    now: float,
) -> int:
    if not dry_run and not notifiers:
        print(
            "没有配置通知渠道。请设置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID，" "或使用 --dry-run。"
        )
        return 2

    quotes, bundle = _gather(config, state, now)
    normalized = normalize_quotes(quotes, sanity=config.sanity_bands())
    collapsed = collapse_source_quotes(normalized)
    by_coin: dict[str, list[Quote]] = defaultdict(list)
    for quote in collapsed:
        by_coin[quote.coin].append(quote)

    observations: dict[str, Observation] = {}
    peg_notes: dict[str, str] = {}
    for coin in config.enabled_coins():
        if coin.peg_mode != "usd":
            continue
        observations[coin.symbol] = _observe(coin, by_coin[coin.symbol], coin.peg, "锚定")
    for coin in config.enabled_coins():
        if coin.peg_mode != "vault" or coin.vault is None:
            continue
        peg, note = _vault_peg(coin, observations, by_coin, bundle, config)
        peg_notes[coin.symbol] = note
        observations[coin.symbol] = _observe(coin, by_coin[coin.symbol], peg, "金库公允价值")

    blocks = ["== depeg-monitor cycle =="]
    for coin in config.enabled_coins():
        obs = observations[coin.symbol]
        blocks.append(summary_block(coin.symbol, _header(coin, obs), by_coin[coin.symbol]))
    if bundle.failures:
        blocks.append("context failures:")
        blocks.extend(f"  {item}" for item in bundle.failures)
    else:
        blocks.append(
            "context ok: "
            f"supplies {len(bundle.supply)}, pools {len(bundle.pools)}, "
            f"funding {len(bundle.funding)}, vaults {len(bundle.vault_rates)}"
        )
    print(format_summary(blocks))

    snapshot = {
        key: Episode.from_dict(episode.to_dict()) for key, episode in state.episodes.items()
    }
    outbound: list[_Outbound] = []
    for coin in config.enabled_coins():
        outbound.extend(
            _decide_coin(
                config,
                state,
                coin,
                observations[coin.symbol],
                by_coin[coin.symbol],
                bundle,
                peg_notes.get(coin.symbol, ""),
                now,
            )
        )

    _remember_context(state, bundle, now)
    state.updated_at = now

    if not outbound:
        print("告警：无")
    elif dry_run:
        print("告警（dry-run，未发送）：")
        for item in outbound:
            print("---")
            print(item.text)
    else:
        for item in outbound:
            if deliver(notifiers, item.text):  # type: ignore[arg-type]
                print(f"已发送 {', '.join(item.keys)}")
            else:
                log.error("alert was not delivered for %s", ", ".join(item.keys))
                for key in item.keys:
                    if key in snapshot:
                        state.episodes[key] = snapshot[key]
                    else:
                        state.episodes.pop(key, None)
                print(f"发送失败 {', '.join(item.keys)}")

    if save:
        save_state(state_path, state)
    return 0


def _gather(
    config: AppConfig, state: MonitorState, now: float
) -> tuple[list[Quote], ContextBundle]:
    from depeg_monitor.config import rpc_urls

    timeout = httpx.Timeout(20.0, connect=5.0)
    headers = {"user-agent": f"depeg-monitor/{__version__} (alert-only)"}
    quotes: list[Quote] = []
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        rpc = RpcClient(client, {chain: rpc_urls(config, chain) for chain in config.chains})
        ctx = FetchContext(client=client, rpc=rpc, config=config, now=now)
        sources = build_sources(config)
        bundle = ContextBundle(failures=["context did not run"])
        with ThreadPoolExecutor(max_workers=8) as pool:
            source_futures = {pool.submit(source.fetch, ctx): source for source in sources}  # type: ignore[attr-defined]
            context_future = pool.submit(collect_context, ctx, state)
            for future in as_completed(source_futures):
                source = source_futures[future]
                name = getattr(source, "name", "source")
                try:
                    quotes.extend(future.result())
                except Exception as exc:
                    log.exception("source %s crashed", name)
                    quotes.append(
                        Quote(
                            coin="*",
                            source=str(name),
                            category="aggregator",
                            ok=False,
                            error=short_err(exc),
                        )
                    )
            try:
                bundle = context_future.result()
            except Exception as exc:
                log.exception("context collection crashed")
                bundle = ContextBundle(failures=[f"context crashed: {short_err(exc)}"])
    return quotes, bundle


def _decide_coin(
    config: AppConfig,
    state: MonitorState,
    coin: CoinSpec,
    obs: Observation,
    quotes: list[Quote],
    bundle: ContextBundle,
    peg_note: str,
    now: float,
) -> list[_Outbound]:
    lines, signal_values = coin_signals(
        bundle, coin.symbol, funding_coins=config.heuristics.funding_coins
    )
    if peg_note:
        lines.append(peg_note)
    rationale = build_rationale(
        groups=obs.groups.medians,
        peg=obs.peg,
        spread_bps=obs.groups.spread_bps,
        divergence_bps=config.divergence_bps,
        signals=signal_values,
        supply_change_pct=config.heuristics.supply_change_pct,
        imbalance_excess_pp=config.heuristics.imbalance_excess_pp,
        imbalance_jump_pp=config.heuristics.imbalance_jump_pp,
        depth_drop_pct=config.heuristics.depth_drop_pct,
        funding_stress_rate=config.heuristics.funding_stress_rate,
    )
    level_for_cooldown = obs.level or "info"
    depeg = decide(
        state.episode(coin.symbol),
        data_ok=obs.data_ok,
        level=obs.level,
        magnitude=obs.deviation_bps,
        recovered=bool(
            obs.data_ok
            and obs.deviation_bps is not None
            and obs.deviation_bps <= coin.thresholds.recovery
        ),
        now=now,
        cooldown_s=coin.thresholds.cooldown[level_for_cooldown],
        price=obs.price,
        peg=obs.peg,
    )
    spread = obs.groups.spread_bps
    divergence_ok = bool(obs.data_ok and spread is not None)
    divergence = decide(
        state.episode(f"{coin.symbol}#divergence"),
        data_ok=divergence_ok,
        level="info"
        if divergence_ok and spread is not None and spread >= config.divergence_bps
        else None,
        magnitude=spread,
        recovered=bool(
            divergence_ok and spread is not None and spread <= config.divergence_bps * 0.5
        ),
        now=now,
        cooldown_s=coin.thresholds.cooldown["info"],
        price=obs.price,
        peg=obs.peg,
    )
    _store(state, coin.symbol, depeg.episode)
    _store(state, f"{coin.symbol}#divergence", divergence.episode)

    messages: list[_Outbound] = []
    if (
        depeg.notify
        and obs.price is not None
        and obs.peg is not None
        and obs.deviation_bps is not None
    ):
        if depeg.action == "recover":
            text = format_recovery(
                coin=coin.symbol,
                price=obs.price,
                peg=obs.peg,
                peg_label=obs.peg_label,
                deviation_bps=obs.deviation_bps,
                recovery_bps=coin.thresholds.recovery,
                previous_severity=depeg.previous_severity,
                since=depeg.since,
                now=now,
                groups=obs.groups.medians,
                spread_bps=spread,
                divergence_bps=config.divergence_bps,
            )
        elif obs.level is not None:
            text = format_depeg(
                coin=coin.symbol,
                action=depeg.action,
                severity=obs.level,
                previous_severity=depeg.previous_severity,
                price=obs.price,
                peg=obs.peg,
                peg_label=obs.peg_label,
                deviation_bps=obs.deviation_bps,
                direction=obs.direction_zh,
                usable=obs.usable,
                categories=obs.categories,
                info_bps=coin.thresholds.info,
                warning_bps=coin.thresholds.warning,
                critical_bps=coin.thresholds.critical,
                groups=obs.groups.medians,
                spread_bps=spread,
                divergence_bps=config.divergence_bps,
                quotes=quotes,
                context_lines=lines,
                rationale=rationale.text,
                now=now,
            )
        else:
            text = ""
        if text:
            messages.append(_Outbound(text=text, keys=[coin.symbol]))
    if (
        divergence.notify
        and not messages
        and obs.price is not None
        and obs.peg is not None
        and obs.deviation_bps is not None
        and spread is not None
    ):
        text = format_divergence(
            coin=coin.symbol,
            price=obs.price,
            peg=obs.peg,
            peg_label=obs.peg_label,
            deviation_bps=obs.deviation_bps,
            groups=obs.groups.medians,
            spread_bps=spread,
            divergence_bps=config.divergence_bps,
            rationale=rationale.text,
            now=now,
            action=divergence.action,
        )
        messages.append(_Outbound(text=text, keys=[f"{coin.symbol}#divergence"]))
    return messages


def _remember_context(state: MonitorState, bundle: ContextBundle, now: float) -> None:
    for coin, supply in bundle.supply.items():
        append_supply(state, coin, supply, now)
    for pool in bundle.pools:
        state.pool_excess_pp[pool.name] = dict(pool.excess_pp)
        state.pool_depth[pool.name] = pool.depth


def _store(state: MonitorState, key: str, episode: Episode) -> None:
    if episode.active:
        state.episodes[key] = episode
    else:
        state.episodes.pop(key, None)


def _observe(coin: CoinSpec, quotes: list[Quote], peg: float | None, peg_label: str) -> Observation:
    return observe(
        coin.symbol,
        quotes,
        peg=peg,
        peg_label=peg_label,
        sanity_min=coin.sanity_min,
        sanity_max=coin.sanity_max,
        min_sources=coin.min_sources,
        min_categories=coin.min_categories,
        info_bps=coin.thresholds.info,
        warning_bps=coin.thresholds.warning,
        critical_bps=coin.thresholds.critical,
    )


def _vault_peg(
    coin: CoinSpec,
    observations: dict[str, Observation],
    by_coin: dict[str, list[Quote]],
    bundle: ContextBundle,
    config: AppConfig,
) -> tuple[float | None, str]:
    assert coin.vault is not None
    rate = bundle.vault_rates.get(coin.symbol)
    if rate is None:
        return None, "金库汇率本轮不可用，跳过该币的告警判定。"
    ref, how = _reference_price(coin.vault.numeraire, observations, by_coin, config)
    if ref is None:
        return None, f"{coin.vault.numeraire} 参考价不可用，跳过公允价值。"
    return rate * ref, (
        f"公允价值使用 {coin.vault.numeraire} {how} {ref:.6f} × 金库汇率 {rate:.6f}。"
    )


def _reference_price(
    symbol: str,
    observations: dict[str, Observation],
    by_coin: dict[str, list[Quote]],
    config: AppConfig,
) -> tuple[float | None, str]:
    current = observations.get(symbol)
    if current is not None and current.data_ok and current.price is not None:
        return current.price, "共识价"
    spec = config.coin(symbol)
    low, high = (spec.sanity_min, spec.sanity_max) if spec else (0.5, 1.5)
    usable = [
        quote.price
        for quote in by_coin.get(symbol, [])
        if quote.ok
        and quote.price is not None
        and quote.quote_asset == "USD"
        and low <= quote.price <= high
    ]
    if not usable:
        return None, "缺失"
    return median([price for price in usable if price is not None]), "不完整参考价"


def _header(coin: CoinSpec, obs: Observation) -> str:
    price = f"{obs.price:.6f}" if obs.price is not None else "     n/a"
    deviation = f"{obs.deviation_bps:7.1f} bps" if obs.deviation_bps is not None else "    n/a"
    level = obs.level or ("inside" if obs.data_ok else "no-alert")
    return (
        f"{coin.symbol:<6} price {price}  dev {deviation}  "
        f"sources {obs.usable}/{obs.min_sources}  categories {obs.categories}/{obs.min_categories}  "
        f"{level}  ({obs.reason})"
    )
