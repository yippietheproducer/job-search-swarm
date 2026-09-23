"""Testy silnika lejka aplikacji (pipeline.py)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pracuj_ai.pipeline import (
    FOLLOWUP_AFTER_DAYS,
    FOLLOWUP2_AFTER_DAYS,
    STATES,
    Pipeline,
)


@pytest.fixture()
def pipe(tmp_path):
    return Pipeline(str(tmp_path / "test.db"))


def test_add_creates_sent_application(pipe):
    app_id = pipe.add(None, "Acme", "Junior React", "cv_frontend.md")
    rows = pipe.list(("sent",))
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme"
    assert rows[0]["state"] == "sent"


def test_happy_path_transitions(pipe):
    app_id = pipe.add(None, "Acme", "Junior React")
    for state in ("screening", "interview", "offer"):
        pipe.transition(app_id, state)
    assert pipe.list(("offer",))[0]["id"] == app_id


def test_illegal_transition_rejected(pipe):
    app_id = pipe.add(None, "Acme", "Junior React")
    with pytest.raises(ValueError):
        pipe.transition(app_id, "offer")  # sent -> offer nie istnieje


def test_unknown_state_raises(pipe):
    app_id = pipe.add(None, "Acme", "Junior React")
    with pytest.raises(ValueError):
        pipe.transition(app_id, "dreaming")


def test_missing_app_raises(pipe):
    with pytest.raises(LookupError):
        pipe.transition(9999, "rejected")


def test_followups_due_after_threshold(pipe):
    app_id = pipe.add(None, "OldCo", "Junior X")
    old = (datetime.now() - timedelta(days=FOLLOWUP_AFTER_DAYS + 1)).isoformat(timespec="seconds")
    pipe.conn.execute("UPDATE applications SET applied_at=? WHERE id=?", (old, app_id))
    pipe.conn.commit()

    fresh_id = pipe.add(None, "NewCo", "Junior Y")  # świeża — nie może się pojawić

    due = pipe.followups_due()
    ids = [d["row"]["id"] for d in due]
    assert app_id in ids
    assert fresh_id not in ids
    scen = next(d for d in due if d["row"]["id"] == app_id)
    assert scen["scenario"] == 1  # 5 dni < 10 => scenariusz 1


def test_followup_scenario2_after_10_days(pipe):
    app_id = pipe.add(None, "GhostCo", "Junior Z")
    old = (datetime.now() - timedelta(days=FOLLOWUP2_AFTER_DAYS + 2)).isoformat(timespec="seconds")
    pipe.conn.execute("UPDATE applications SET applied_at=? WHERE id=?", (old, app_id))
    pipe.conn.commit()
    due = pipe.followups_due()
    assert due[0]["scenario"] == 2


def test_screening_nudge(pipe):
    app_id = pipe.add(None, "SlowCo", "Junior S")
    pipe.transition(app_id, "screening")
    old = (datetime.now() - timedelta(days=4)).isoformat(timespec="seconds")
    pipe.conn.execute("UPDATE applications SET state_at=? WHERE id=?", (old, app_id))
    pipe.conn.commit()
    due = pipe.followups_due()
    assert any(d["scenario"] == "nudge" and d["row"]["id"] == app_id for d in due)


def test_funnel_rates(pipe):
    a = pipe.add(None, "A", "X"); b = pipe.add(None, "B", "Y")
    pipe.transition(a, "rejected")
    pipe.transition(b, "interview")
    f = pipe.funnel()
    assert f["total"] == 2
    assert f["response_rate"] == 0.5   # interview liczy się jako odpowiedź
    assert f["interview_rate"] == 0.5


def test_all_states_known():
    assert set(STATES) >= {"sent", "followup_1", "screening", "interview", "offer", "rejected"}
