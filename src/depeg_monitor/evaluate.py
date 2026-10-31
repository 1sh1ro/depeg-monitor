"""Severity bands and dedup decisions.

Notifications fire when an episode opens, gets worse, gets better but is
still outside the peg, recovers, or has stayed at the same severity past the
cooldown. Repeats inside the cooldown are suppressed. Missing data never
clears an open episode, so a failed RPC is not reported as a recovery.
"""

from __future__ import annotations

from depeg_monitor.models import SEVERITY_RANK, Decision, Episode

NOTIFY_ACTIONS = frozenset({"open", "escalate", "deescalate", "remind", "recover"})


def classify(deviation_bps: float, *, info: float, warning: float, critical: float) -> str | None:
    """Map a deviation to a tier. The boundary belongs to the higher tier."""
    if deviation_bps >= critical:
        return "critical"
    if deviation_bps >= warning:
        return "warning"
    if deviation_bps >= info:
        return "info"
    return None


def decide(
    prev: Episode,
    *,
    data_ok: bool,
    level: str | None,
    magnitude: float | None,
    recovered: bool,
    now: float,
    cooldown_s: int | None,
    price: float | None = None,
    peg: float | None = None,
) -> Decision:
    """Pure state transition for one alert episode."""
    if not data_ok:
        return Decision("skip", False, prev, prev.severity, prev.since)

    if prev.active and recovered:
        cleared = Episode()
        return Decision("recover", True, cleared, prev.severity, prev.since)

    if not prev.active:
        if level is None:
            return Decision("none", False, Episode(), None, None)
        opened = _touch(prev, level, magnitude, now, price, peg, reset_since=True)
        return Decision("open", True, opened, None, opened.since)

    # Still inside an episode. The band between recovery and the info tier
    # keeps the episode open without a new page.
    if level is None:
        held = _touch(prev, prev.severity, magnitude, now, price, peg, reset_since=False)
        held.last_notified_at = prev.last_notified_at
        return Decision("hold", False, held, prev.severity, prev.since)

    prev_rank = SEVERITY_RANK.get(prev.severity or "", 0)
    new_rank = SEVERITY_RANK.get(level, 0)
    if new_rank > prev_rank:
        updated = _touch(prev, level, magnitude, now, price, peg, reset_since=False)
        return Decision("escalate", True, updated, prev.severity, prev.since)
    if new_rank < prev_rank:
        updated = _touch(prev, level, magnitude, now, price, peg, reset_since=False)
        return Decision("deescalate", True, updated, prev.severity, prev.since)

    elapsed = now - (prev.last_notified_at or 0)
    wait = cooldown_s if cooldown_s is not None else 0
    if elapsed >= wait:
        updated = _touch(prev, level, magnitude, now, price, peg, reset_since=False)
        return Decision("remind", True, updated, prev.severity, prev.since)
    held = _touch(prev, level, magnitude, now, price, peg, reset_since=False)
    held.last_notified_at = prev.last_notified_at
    return Decision("hold", False, held, prev.severity, prev.since)


def _touch(
    prev: Episode,
    level: str | None,
    magnitude: float | None,
    now: float,
    price: float | None,
    peg: float | None,
    *,
    reset_since: bool,
) -> Episode:
    return Episode(
        active=True,
        severity=level,
        since=now if reset_since or prev.since is None else prev.since,
        last_notified_at=now,
        last_magnitude=magnitude,
        last_price=price if price is not None else prev.last_price,
        peg=peg if peg is not None else prev.peg,
    )
