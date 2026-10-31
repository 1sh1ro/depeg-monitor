from depeg_monitor.rationale import build_rationale, oracle_lag


def test_oracle_lag_requires_same_direction_and_a_closer_feed():
    assert oracle_lag({"chainlink": 1.0, "dex": 0.99, "cex": 0.991}, 1.0, 90, 40)
    assert not oracle_lag({"chainlink": 0.99, "dex": 1.0, "cex": 1.001}, 1.0, 90, 40)
    assert not oracle_lag({"chainlink": 1.0, "dex": 0.99, "cex": 1.01}, 1.0, 200, 40)


def test_one_sided_flow_needs_a_jump_not_just_a_chronic_imbalance():
    chronic = build_rationale(
        groups={"chainlink": 0.99, "dex": 0.99, "cex": 0.99},
        peg=1.0,
        spread_bps=5,
        divergence_bps=40,
        signals={"pool": {"max_excess_pp": 36.0, "max_jump_pp": 0.2, "max_depth_drop_pct": None}},
        supply_change_pct=3,
        imbalance_excess_pp=20,
        imbalance_jump_pp=5,
        depth_drop_pct=20,
        funding_stress_rate=-0.0002,
    )
    assert "one_sided_flow" not in chronic.tags
    fresh = build_rationale(
        groups={"chainlink": 0.99, "dex": 0.99, "cex": 0.99},
        peg=1.0,
        spread_bps=5,
        divergence_bps=40,
        signals={"pool": {"max_excess_pp": 36.0, "max_jump_pp": 8.0, "max_depth_drop_pct": None}},
        supply_change_pct=3,
        imbalance_excess_pp=20,
        imbalance_jump_pp=5,
        depth_drop_pct=20,
        funding_stress_rate=-0.0002,
    )
    assert "one_sided_flow" in fresh.tags
    assert "不是投资建议" in fresh.text


def test_supply_funding_and_mixed_fallback():
    stressed = build_rationale(
        groups={},
        peg=1.0,
        spread_bps=None,
        divergence_bps=40,
        signals={
            "supply": {"change_pct": -4.0, "hours": 24},
            "funding": {"rates": [-0.0003, -0.0004]},
        },
        supply_change_pct=3,
        imbalance_excess_pp=20,
        imbalance_jump_pp=5,
        depth_drop_pct=20,
        funding_stress_rate=-0.0002,
    )
    assert "structural_supply" in stressed.tags
    assert "funding_stress" in stressed.tags
    quiet = build_rationale(
        groups={},
        peg=1.0,
        spread_bps=None,
        divergence_bps=40,
        signals={"funding": {"rates": [-0.0003]}},
        supply_change_pct=3,
        imbalance_excess_pp=20,
        imbalance_jump_pp=5,
        depth_drop_pct=20,
        funding_stress_rate=-0.0002,
    )
    assert quiet.tags == []
    assert "不足以区分" in quiet.text
