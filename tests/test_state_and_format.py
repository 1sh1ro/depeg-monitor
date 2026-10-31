import json
from pathlib import Path

import pytest

from depeg_monitor.formatting import DISCLAIMER, format_depeg, format_recovery
from depeg_monitor.models import Quote, SupplyPoint
from depeg_monitor.state import append_supply, load_state, preview_supply_change, save_state


def test_state_roundtrip_and_unknown_version(tmp_path: Path):
    path = tmp_path / "state.json"
    state = load_state(path)
    append_supply(state, "USDC", 100.0, now=1_000)
    append_supply(state, "USDC", 103.0, now=1_000 + 86_400)
    state.episodes["USDC"] = state.episode("USDC")
    state.pool_depth["3pool"] = 10.0
    save_state(path, state)
    loaded = load_state(path)
    assert loaded.supply["USDC"][-1].v == 103.0
    assert loaded.pool_depth["3pool"] == 10.0
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert load_state(path).supply == {}


def test_supply_change_uses_the_sample_nearest_the_window():
    points = [
        SupplyPoint(t=0, v=100),
        SupplyPoint(t=3_600, v=100),
        SupplyPoint(t=90_000, v=110),
    ]
    change = preview_supply_change(
        points, 110, now=90_000, window_seconds=86_400, min_interval_seconds=60
    )
    assert change is not None
    percent, hours = change
    assert percent == pytest.approx(10)
    assert hours == pytest.approx(24)


def test_alert_text_is_chinese_and_not_advice():
    text = format_depeg(
        coin="USDC",
        action="open",
        severity="warning",
        previous_severity=None,
        price=0.995,
        peg=1.0,
        peg_label="锚定",
        deviation_bps=50,
        direction="低于锚定",
        usable=6,
        categories=4,
        info_bps=15,
        warning_bps=40,
        critical_bps=80,
        groups={"chainlink": 0.999, "dex": 0.994, "cex": 0.995},
        spread_bps=50,
        divergence_bps=40,
        quotes=[Quote(coin="USDC", source="chainlink", category="chainlink", ok=True, price=0.999)],
        context_lines=["供应约 478.8 亿。"],
        rationale="现有背景数据不足以区分短期波动和持续性问题。以上是数据线索，不是投资建议。",
        now=1_793_438_400,
    )
    assert "【脱锚预警 · 警告 · 新预警】USDC" in text
    assert "50.0 bps" in text
    assert DISCLAIMER in text
    recovery = format_recovery(
        coin="USDC",
        price=0.9997,
        peg=1.0,
        peg_label="锚定",
        deviation_bps=3,
        recovery_bps=8,
        previous_severity="warning",
        since=1_793_438_400 - 1500,
        now=1_793_438_400,
        groups={},
        spread_bps=None,
        divergence_bps=40,
    )
    assert "【回归锚定】USDC" in recovery
    assert "警告" in recovery
    assert "不是投资建议" in recovery
