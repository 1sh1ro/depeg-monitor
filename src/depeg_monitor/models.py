"""Shared data types. No I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Category = Literal["chainlink", "dex", "cex", "aggregator"]
PegMode = Literal["usd", "vault"]
Severity = Literal["info", "warning", "critical"]

SEVERITY_RANK: dict[str, int] = {"info": 1, "warning": 2, "critical": 3}
SEVERITY_ZH: dict[str, str] = {"info": "提示", "warning": "警告", "critical": "严重"}


@dataclass(frozen=True)
class Quote:
    """One price observation from one source for one coin.

    ``price`` is in ``quote_asset`` per 1 coin. USD quotes are compared with
    the peg directly. Stablecoin quotes (USDT, USDC, ...) are scaled by that
    stablecoin's own USD reference before consensus, when one exists.
    """

    coin: str
    source: str
    category: Category
    ok: bool
    price: float | None = None
    quote_asset: str = "USD"
    error: str | None = None
    detail: str | None = None
    observed_at: float | None = None


@dataclass(frozen=True)
class GroupSpread:
    """Median price of each independent venue family, and the gap between them."""

    medians: dict[str, float]
    spread_bps: float | None


@dataclass(frozen=True)
class Observation:
    """Consensus view for one coin on one cycle. ``data_ok`` gates alerting."""

    coin: str
    data_ok: bool
    reason: str
    price: float | None
    peg: float | None
    peg_label: str
    deviation_bps: float | None
    level: str | None
    usable: int
    categories: int
    min_sources: int
    min_categories: int
    groups: GroupSpread
    direction_zh: str


@dataclass
class Episode:
    """Dedup state for one alert stream (a depeg, or a divergence flag)."""

    active: bool = False
    severity: str | None = None
    since: float | None = None
    last_notified_at: float | None = None
    last_magnitude: float | None = None
    last_price: float | None = None
    peg: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "severity": self.severity,
            "since": self.since,
            "last_notified_at": self.last_notified_at,
            "last_magnitude": self.last_magnitude,
            "last_price": self.last_price,
            "peg": self.peg,
        }

    @classmethod
    def from_dict(cls, raw: object) -> Episode:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            active=bool(raw.get("active", False)),
            severity=_opt_str(raw.get("severity")),
            since=_opt_float(raw.get("since")),
            last_notified_at=_opt_float(raw.get("last_notified_at")),
            last_magnitude=_opt_float(raw.get("last_magnitude")),
            last_price=_opt_float(raw.get("last_price")),
            peg=_opt_float(raw.get("peg")),
        )


@dataclass(frozen=True)
class Decision:
    action: str
    notify: bool
    episode: Episode
    previous_severity: str | None
    since: float | None


@dataclass
class SupplyPoint:
    t: float
    v: float

    def to_dict(self) -> dict[str, float]:
        return {"t": self.t, "v": self.v}


@dataclass
class MonitorState:
    """Persistent dedup, recovery, and context history."""

    version: int = 1
    episodes: dict[str, Episode] = field(default_factory=dict)
    supply: dict[str, list[SupplyPoint]] = field(default_factory=dict)
    pool_excess_pp: dict[str, dict[str, float]] = field(default_factory=dict)
    pool_depth: dict[str, float] = field(default_factory=dict)
    updated_at: float | None = None

    def episode(self, key: str) -> Episode:
        return self.episodes.get(key, Episode())

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "episodes": {key: ep.to_dict() for key, ep in sorted(self.episodes.items())},
            "supply": {
                coin: [point.to_dict() for point in points]
                for coin, points in sorted(self.supply.items())
            },
            "pool_excess_pp": {k: dict(v) for k, v in sorted(self.pool_excess_pp.items())},
            "pool_depth": dict(sorted(self.pool_depth.items())),
        }

    @classmethod
    def from_dict(cls, raw: object) -> MonitorState | None:
        """Return None when the payload is not version 1, so callers can reset."""
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return None
        episodes: dict[str, Episode] = {}
        raw_eps = raw.get("episodes") or {}
        if isinstance(raw_eps, dict):
            for key, value in raw_eps.items():
                episodes[str(key)] = Episode.from_dict(value)
        supply: dict[str, list[SupplyPoint]] = {}
        raw_supply = raw.get("supply") or {}
        if isinstance(raw_supply, dict):
            for coin, points in raw_supply.items():
                parsed: list[SupplyPoint] = []
                if isinstance(points, list):
                    for point in points:
                        if isinstance(point, dict) and "t" in point and "v" in point:
                            parsed.append(SupplyPoint(t=float(point["t"]), v=float(point["v"])))
                supply[str(coin)] = parsed
        excess: dict[str, dict[str, float]] = {}
        raw_excess = raw.get("pool_excess_pp") or {}
        if isinstance(raw_excess, dict):
            for pool, shares in raw_excess.items():
                if isinstance(shares, dict):
                    excess[str(pool)] = {str(k): float(v) for k, v in shares.items()}
        depth: dict[str, float] = {}
        raw_depth = raw.get("pool_depth") or {}
        if isinstance(raw_depth, dict):
            depth = {str(k): float(v) for k, v in raw_depth.items()}
        updated = raw.get("updated_at")
        return cls(
            episodes=episodes,
            supply=supply,
            pool_excess_pp=excess,
            pool_depth=depth,
            updated_at=float(updated) if isinstance(updated, int | float) else None,
        )


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
