"""JSON state file so cooldown and recovery survive a GitHub Actions cron."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from depeg_monitor.models import MonitorState, SupplyPoint

log = logging.getLogger(__name__)

SUPPLY_RETENTION_SECONDS = 48 * 3600


def load_state(path: Path) -> MonitorState:
    if not path.exists():
        log.info("no state file at %s, starting fresh", path)
        return MonitorState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("state file %s is unreadable (%s); starting fresh", path, exc)
        return MonitorState()
    parsed = MonitorState.from_dict(raw)
    if parsed is None:
        log.warning("state file %s has an unknown version; starting fresh", path)
        return MonitorState()
    return parsed


def save_state(path: Path, state: MonitorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    tmp.replace(path)


def append_supply(
    state: MonitorState,
    coin: str,
    supply: float,
    now: float,
    *,
    min_interval_seconds: float = 60,
) -> None:
    """Keep a 48h ring of supply samples. Skip samples closer than a minute."""
    points = state.supply.setdefault(coin, [])
    if points and now - points[-1].t < min_interval_seconds:
        return
    points.append(SupplyPoint(t=now, v=supply))
    cutoff = now - SUPPLY_RETENTION_SECONDS
    state.supply[coin] = [point for point in points if point.t >= cutoff]


def preview_supply_change(
    points: list[SupplyPoint],
    supply: float,
    now: float,
    window_seconds: float,
    min_interval_seconds: float = 60,
) -> tuple[float, float] | None:
    """Change versus history, treating ``supply`` as the newest sample."""
    series = list(points)
    if series and now - series[-1].t < min_interval_seconds:
        series[-1] = SupplyPoint(t=now, v=supply)
    else:
        series.append(SupplyPoint(t=now, v=supply))
    return supply_change(series, now, window_seconds)


def supply_change(
    points: list[SupplyPoint],
    now: float,
    window_seconds: float,
) -> tuple[float, float] | None:
    """Percent change versus the sample closest to ``window_seconds`` ago.

    Returns ``(percent, hours)``. When every sample is newer than the window,
    the oldest sample is the baseline and ``hours`` is shorter than requested.
    """
    if len(points) < 2:
        return None
    target = now - window_seconds
    older = [point for point in points if point.t <= target]
    base = (
        max(older, key=lambda point: point.t) if older else min(points, key=lambda point: point.t)
    )
    latest = max(points, key=lambda point: point.t)
    if latest.t <= base.t or base.v <= 0:
        return None
    percent = (latest.v - base.v) / base.v * 100
    hours = (latest.t - base.t) / 3600
    return percent, hours
