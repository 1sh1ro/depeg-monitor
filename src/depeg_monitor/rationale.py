"""Turn context numbers into a short Chinese note.

The note describes the shape of the data. It is not a trading recommendation
and it does not claim that a stablecoin is solvent or insolvent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rationale:
    tags: list[str]
    text: str


def build_rationale(
    *,
    groups: dict[str, float],
    peg: float | None,
    spread_bps: float | None,
    divergence_bps: float,
    signals: dict[str, dict[str, object]],
    supply_change_pct: float,
    imbalance_excess_pp: float,
    imbalance_jump_pp: float,
    depth_drop_pct: float,
    funding_stress_rate: float,
) -> Rationale:
    tags: list[str] = []
    parts: list[str] = []
    if peg is not None and oracle_lag(groups, peg, spread_bps, divergence_bps):
        tags.append("oracle_lag")
        parts.append(
            "链上预言机比 DEX 和 CEX 更接近锚定，而且场内外价格同向偏离。"
            "数据上更像预言机还没跟上，不是预言机已经确认脱锚。"
        )
    supply = signals.get("supply") or {}
    change = supply.get("change_pct")
    if isinstance(change, int | float) and abs(float(change)) >= supply_change_pct:
        tags.append("structural_supply")
        parts.append(
            "供应在观察窗口里变化较大。供应上升通常对应净铸造，下降通常对应净赎回，"
            "这本身不能说明储备是否充足。"
        )
    pool = signals.get("pool") or {}
    excess = pool.get("max_excess_pp")
    jump = pool.get("max_jump_pp")
    if (
        isinstance(excess, int | float)
        and float(excess) >= imbalance_excess_pp
        and isinstance(jump, int | float)
        and float(jump) >= imbalance_jump_pp
    ):
        tags.append("one_sided_flow")
        parts.append(
            "该币在监控池里的占比明显高于均衡，而且比上一份快照又升高了一截，"
            "更像新的单边卖出，而不是池子长期停在旧的不平衡上。"
        )
    drop = pool.get("max_depth_drop_pct")
    if isinstance(drop, int | float) and float(drop) >= depth_drop_pct:
        tags.append("liquidity_drop")
        parts.append("监控池的代币存量比上次快照下降较多，可交易深度变薄。")
    funding = signals.get("funding") or {}
    rates = funding.get("rates")
    if (
        isinstance(rates, list)
        and len(rates) >= 2
        and all(
            isinstance(rate, int | float) and float(rate) <= funding_stress_rate for rate in rates
        )
    ):
        tags.append("funding_stress")
        parts.append(
            "配置的永续资金费都偏负，空头对冲正在付费。"
            "对依赖永续对冲的稳定币，这是已知压力来源，不是已经失效的结论。"
        )
    if not parts:
        parts.append("现有背景数据不足以区分短期波动和持续性问题。")
    parts.append("以上是数据线索，不是投资建议。")
    return Rationale(tags=tags, text="".join(parts))


def oracle_lag(
    groups: dict[str, float],
    peg: float,
    spread_bps: float | None,
    threshold_bps: float,
) -> bool:
    """Chainlink is nearer the peg while DEX and CEX have moved the same way."""
    if peg <= 0 or spread_bps is None or spread_bps < threshold_bps:
        return False
    if "chainlink" not in groups:
        return False
    others = [name for name in ("dex", "cex") if name in groups]
    if not others:
        return False
    chainlink_dev = abs(groups["chainlink"] - peg) / peg
    if not all(abs(groups[name] - peg) / peg > chainlink_dev for name in others):
        return False
    sides = [groups[name] - peg for name in others]
    return all(side > 0 for side in sides) or all(side < 0 for side in sides)
