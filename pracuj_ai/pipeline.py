"""Application pipeline — the funnel SSOT.

An APPLICATION is the act of sending tailored materials for an offer.
Offers are supply; applications are the unit of work this product optimizes.

States (one-way except `withdrawn`):
    sent        -> wysłane, czekamy
    followup_1  -> follow-up #1 poszedł (dzień 4)
    screening   -> rekruter odpowiedział / rozmowa HR
    interview   -> rozmowa techniczna/finalna
    offer       -> oferta
    rejected    -> koniec ścieżki
    withdrawn   -> my wycofani

Follow-up rules (z badań SELL_STRATEGY_AUG2026: 65% odpowiedzi po follow-upie):
    brak odpowiedzi 4 dni po `sent`      -> FOLLOWUP_DUE (scenariusz 1)
    brak odpowiedzi 10 dni po `sent`     -> FOLLOWUP_DUE (scenariusz 2)
    3 dni po `screening` bez następnego  -> nudge
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

STATES = ("sent", "followup_1", "screening", "interview", "offer", "rejected", "withdrawn")
_TRANSITIONS = {
    "sent": {"followup_1", "screening", "interview", "rejected", "withdrawn"},
    "followup_1": {"screening", "interview", "offer", "rejected", "withdrawn"},
    "screening": {"interview", "offer", "rejected", "withdrawn"},
    "interview": {"offer", "rejected", "withdrawn"},
    "offer": {"rejected", "withdrawn"},  # offer może być odrzucona przez nas lub ich
}
FOLLOWUP_AFTER_DAYS = 4
FOLLOWUP2_AFTER_DAYS = 10
SCREENING_NUDGE_DAYS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id    TEXT REFERENCES offers(id),
    company     TEXT,
    role        TEXT,
    cv_variant  TEXT,
    applied_at  TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'sent',
    state_at    TEXT NOT NULL,
    next_action TEXT,
    notes       TEXT,
    UNIQUE(offer_id, applied_at)
);
CREATE INDEX IF NOT EXISTS idx_applications_state ON applications(state);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Pipeline:
    def __init__(self, db_path: str = "offers.db"):
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def add(self, offer_id: str | None, company: str, role: str,
            cv_variant: str = "", notes: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO applications (offer_id, company, role, cv_variant,
               applied_at, state, state_at, notes)
               VALUES (?,?,?,?,?,'sent',?,?)""",
            (offer_id, company, role, cv_variant, _now(), _now(), notes))
        self.conn.commit()
        return int(cur.lastrowid)

    def transition(self, app_id: int, new_state: str) -> None:
        if new_state not in STATES:
            raise ValueError(f"unknown state {new_state!r}")
        row = self.conn.execute(
            "SELECT state FROM applications WHERE id=?", (app_id,)).fetchone()
        if row is None:
            raise LookupError(f"application {app_id} not found")
        old = row["state"]
        allowed = _TRANSITIONS.get(old, set())
        if old != new_state and new_state not in allowed:
            raise ValueError(f"illegal transition {old} -> {new_state}")
        self.conn.execute(
            "UPDATE applications SET state=?, state_at=? WHERE id=?",
            (new_state, _now(), app_id))
        self.conn.commit()

    def list(self, states: tuple[str, ...] = STATES) -> list[sqlite3.Row]:
        q = ",".join("?" * len(states))
        return self.conn.execute(
            f"SELECT * FROM applications WHERE state IN ({q}) ORDER BY applied_at",
            states).fetchall()

    # ---- silnik follow-upów -------------------------------------------------
    def followups_due(self, today: datetime | None = None) -> list[dict]:
        today = today or datetime.now()
        due: list[dict] = []
        for r in self.list(("sent",)):
            age_days = (today - datetime.fromisoformat(r["applied_at"])).days
            if age_days >= FOLLOWUP_AFTER_DAYS:
                due.append({"row": r, "days": age_days,
                            "scenario": 1 if age_days < FOLLOWUP2_AFTER_DAYS else 2})
        for r in self.list(("screening",)):
            age = (today - datetime.fromisoformat(r["state_at"])).days
            if age >= SCREENING_NUDGE_DAYS:
                due.append({"row": r, "days": age, "scenario": "nudge"})
        return due

    # ---- metryki lejka ------------------------------------------------------
    def funnel(self) -> dict:
        counts = {s: 0 for s in STATES}
        for r in self.conn.execute(
                "SELECT state, COUNT(*) c FROM applications GROUP BY state"):
            counts[r["state"]] = r["c"]
        total = sum(counts.values())
        responses = sum(counts[s] for s in ("screening", "interview", "offer"))
        return {
            "total": total,
            "by_state": counts,
            "response_rate": round(responses / total, 3) if total else None,
            "interview_rate": round((counts["interview"] + counts["offer"]) / total, 3) if total else None,
        }
