"""Chinese alert text. English is limited to the one-line disclaimer."""

from __future__ import annotations

from datetime import UTC, datetime

from depeg_monitor.models import SEVERITY_ZH, Quote

DISCLAIMER = (
    "声明：不是投资建议。本程序只监控、只告警，不持有密钥，不发起任何交易。\n"
    "Not financial advice. Monitoring only."
)

_ACTION_ZH = {
    "open": "新预警",
    "escalate": "升级",
    "deescalate": "降级",
    "remind": "仍未恢复",
}


def format_depeg(
    *,
    coin: str,
    action: str,
    severity: str,
    previous_severity: str | None,
    price: float,
    peg: float,
    peg_label: str,
    deviation_bps: float,
    direction: str,
    usable: int,
    categories: int,
    info_bps: float,
    warning_bps: float,
    critical_bps: float,
    groups: dict[str, float],
    spread_bps: float | None,
    divergence_bps: float,
    quotes: list[Quote],
    context_lines: list[str],
    rationale: str,
    now: float,
) -> str:
    move = _move(action, severity, previous_severity)
    title = f"【脱锚预警 · {SEVERITY_ZH.get(severity, severity)} · {move}】{coin}"
    lines = [
        title,
        (
            f"价格 {_px(price)}，{peg_label} {_px(peg)}，{direction} {deviation_bps:.1f} bps"
            f"（提示 {info_bps:.0f} / 警告 {warning_bps:.0f} / 严重 {critical_bps:.0f}）。"
        ),
        f"共识样本 {usable} 个来源、{categories} 类。低于法定人数不会告警。",
        _group_line(groups, spread_bps, divergence_bps),
        _quote_block(quotes),
    ]
    if context_lines:
        lines.append("背景数据：")
        lines.extend(f"· {line}" for line in context_lines)
    lines.append("数据线索：" + rationale)
    lines.append(f"时间：{_clock(now)}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_recovery(
    *,
    coin: str,
    price: float,
    peg: float,
    peg_label: str,
    deviation_bps: float,
    recovery_bps: float,
    previous_severity: str | None,
    since: float | None,
    now: float,
    groups: dict[str, float],
    spread_bps: float | None,
    divergence_bps: float,
) -> str:
    lasted = _duration(now - since) if since is not None else "未知时长"
    previous = SEVERITY_ZH.get(previous_severity or "", previous_severity or "未知")
    lines = [
        f"【回归锚定】{coin}",
        (
            f"价格 {_px(price)}，{peg_label} {_px(peg)}，偏离 {deviation_bps:.1f} bps，"
            f"已回到恢复阈值 {recovery_bps:.0f} bps 以内。"
        ),
        f"此前级别：{previous}，持续 {lasted}。",
        _group_line(groups, spread_bps, divergence_bps),
        f"时间：{_clock(now)}",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def format_divergence(
    *,
    coin: str,
    price: float,
    peg: float,
    peg_label: str,
    deviation_bps: float,
    groups: dict[str, float],
    spread_bps: float,
    divergence_bps: float,
    rationale: str,
    now: float,
    action: str,
) -> str:
    if action == "recover":
        lines = [
            f"【来源分歧结束】{coin}",
            (
                f"分组差距 {spread_bps:.1f} bps，已低于恢复线 "
                f"{divergence_bps * 0.5:.0f} bps。共识价 {_px(price)}。"
            ),
            _group_line(groups, spread_bps, divergence_bps),
            f"时间：{_clock(now)}",
            DISCLAIMER,
        ]
        return "\n".join(lines)
    move = _move(action, "info", None)
    lines = [
        f"【来源分歧 · 提示 · {move}】{coin}",
        (
            f"共识价 {_px(price)}，相对{peg_label}偏离 {deviation_bps:.1f} bps，"
            "尚未达到该币的脱锚阈值。"
        ),
        _group_line(groups, spread_bps, divergence_bps),
        "数据线索：" + rationale,
        f"时间：{_clock(now)}",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def format_summary(blocks: list[str]) -> str:
    return "\n".join(blocks)


def summary_block(coin: str, observation_text: str, quotes: list[Quote]) -> str:
    rows = [observation_text]
    for quote in quotes:
        if quote.ok and quote.price is not None:
            asset = "" if quote.quote_asset == "USD" else f" {quote.quote_asset}"
            detail = f"  {quote.detail}" if quote.detail else ""
            rows.append(f"  {quote.source:<12} {quote.price:.6f}{asset}{detail}")
        else:
            rows.append(f"  {quote.source:<12} FAIL  {quote.error or 'unknown'}")
    return "\n".join(rows)


def _move(action: str, severity: str, previous: str | None) -> str:
    if action == "escalate" and previous in SEVERITY_ZH:
        return f"由{SEVERITY_ZH[previous]}升至{SEVERITY_ZH.get(severity, severity)}"
    if action == "deescalate" and previous in SEVERITY_ZH:
        return f"由{SEVERITY_ZH[previous]}降至{SEVERITY_ZH.get(severity, severity)}"
    return _ACTION_ZH.get(action, action)


def _group_line(groups: dict[str, float], spread_bps: float | None, threshold: float) -> str:
    if not groups:
        return "分组中位：样本不足。"
    ordered = [name for name in ("chainlink", "dex", "cex") if name in groups]
    parts = [f"{name} {_px(groups[name])}" for name in ordered]
    if spread_bps is None:
        return "分组中位：" + " | ".join(parts) + "。"
    flag = "，超过分歧阈值" if spread_bps >= threshold else ""
    return (
        "分组中位："
        + " | ".join(parts)
        + f"；组间差距 {spread_bps:.1f} bps{flag}（阈值 {threshold:.0f} bps）。"
    )


def _quote_block(quotes: list[Quote]) -> str:
    ok = [quote for quote in quotes if quote.ok and quote.price is not None]
    bad = [quote for quote in quotes if not quote.ok]
    lines = [
        "来源：" + ("、".join(f"{quote.source} {_px(quote.price or 0)}" for quote in ok) or "无")
    ]
    if bad:
        lines.append(
            "失败：" + "、".join(f"{quote.source}（{quote.error or '未知'}）" for quote in bad)
        )
    return "\n".join(lines)


def _px(value: float) -> str:
    return f"{value:.6f}"


def _clock(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟"
    return f"{secs} 秒"
