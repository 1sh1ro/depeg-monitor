from depeg_monitor.evaluate import classify, decide
from depeg_monitor.models import Episode


def _decide(prev, *, deviation, now, data_ok=True, recovery=10, info=20, warning=50, critical=100):
    level = classify(deviation, info=info, warning=warning, critical=critical) if data_ok else None
    cooldown = {"info": 1000, "warning": 500, "critical": 100}
    chosen = level or (prev.severity or "info")
    return decide(
        prev,
        data_ok=data_ok,
        level=level,
        magnitude=deviation,
        recovered=bool(data_ok and deviation <= recovery),
        now=now,
        cooldown_s=cooldown[chosen],
        price=1 - deviation / 10_000,
        peg=1.0,
    )


def test_classify_boundaries():
    assert classify(19.9, info=20, warning=50, critical=100) is None
    assert classify(20, info=20, warning=50, critical=100) == "info"
    assert classify(50, info=20, warning=50, critical=100) == "warning"
    assert classify(100, info=20, warning=50, critical=100) == "critical"


def test_dedup_escalation_and_recovery():
    opened = _decide(Episode(), deviation=25, now=1_000)
    assert opened.action == "open"
    assert opened.notify
    assert opened.episode.active
    assert opened.episode.severity == "info"

    held = _decide(opened.episode, deviation=25, now=1_100)
    assert held.action == "hold"
    assert not held.notify

    reminded = _decide(opened.episode, deviation=25, now=2_000)
    assert reminded.action == "remind"
    assert reminded.notify

    escalated = _decide(reminded.episode, deviation=60, now=2_010)
    assert escalated.action == "escalate"
    assert escalated.notify
    assert escalated.episode.severity == "warning"
    assert escalated.episode.since == opened.episode.since

    eased = _decide(escalated.episode, deviation=30, now=2_020)
    assert eased.action == "deescalate"
    assert eased.episode.severity == "info"

    dead_zone = _decide(eased.episode, deviation=12, now=2_030)
    assert dead_zone.action == "hold"
    assert dead_zone.episode.active
    assert dead_zone.episode.severity == "info"

    recovered = _decide(dead_zone.episode, deviation=9, now=2_200)
    assert recovered.action == "recover"
    assert recovered.notify
    assert not recovered.episode.active
    assert recovered.previous_severity == "info"
    assert recovered.since == opened.episode.since

    quiet = _decide(recovered.episode, deviation=9, now=2_300)
    assert quiet.action == "none"
    assert not quiet.notify


def test_missing_data_does_not_clear_an_open_alert():
    opened = _decide(Episode(), deviation=120, now=0)
    skipped = _decide(opened.episode, deviation=0, now=10, data_ok=False)
    assert skipped.action == "skip"
    assert skipped.episode.active
    assert skipped.episode.severity == "critical"
    assert skipped.episode.last_notified_at == opened.episode.last_notified_at


def test_critical_cooldown_is_shorter_than_info():
    opened = _decide(Episode(), deviation=120, now=0)
    assert _decide(opened.episode, deviation=120, now=99).action == "hold"
    assert _decide(opened.episode, deviation=120, now=100).action == "remind"
