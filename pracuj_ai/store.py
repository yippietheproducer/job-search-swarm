"""SQLite store for monitored offers (dedup, new-detection, remote flag).

Local-first: one file, no server. The monitor and the web dashboard both read
this. Offers are keyed by pracuj.pl offer ID (the digits in `,oferta,<ID>`).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    url         TEXT,
    company     TEXT,
    location    TEXT,
    remote      INTEGER NOT NULL DEFAULT 0,
    city        TEXT,
    fit_score   INTEGER,
    strengths   TEXT,
    gaps        TEXT,
    letter      TEXT,
    cv_bullets  TEXT,
    screening   TEXT,
    first_seen  TEXT,
    last_seen   TEXT,
    status      TEXT NOT NULL DEFAULT 'new',
    funded      INTEGER NOT NULL DEFAULT 0,
    funding_note TEXT,
    raw_text    TEXT,
    source      TEXT,
    cv_personalized TEXT
);
"""

_OFFER_ID_RE = re.compile(r",oferta,(\d+)")


def offer_id(url: str) -> str:
    m = _OFFER_ID_RE.search(url)
    if m:
        return m.group(1)
    # NOT builtin hash(): it is salted per process (PYTHONHASHSEED), so the same
    # URL would mint a different id in every run and dedup/new-detection would
    # silently break across monitor cycles. md5 is stable. (boards.py already
    # did this correctly; this brings the store in line.)
    return "x" + hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


@dataclass
class OfferRecord:
    id: str
    title: str = ""
    url: str = ""
    company: str = ""
    location: str = ""
    remote: bool = False
    city: str = ""
    fit_score: int | None = None
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    letter: str = ""
    cv_bullets: list[str] = field(default_factory=list)
    screening: dict = field(default_factory=dict)
    first_seen: str = ""
    last_seen: str = ""
    status: str = "new"
    funded: bool = False
    funding_note: str = ""
    raw_text: str = ""
    source: str = ""
    cv_personalized: str = ""

    def to_row(self) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "company": self.company,
            "location": self.location,
            "remote": 1 if self.remote else 0,
            "city": self.city,
            "fit_score": self.fit_score,
            "strengths": json.dumps(self.strengths, ensure_ascii=False),
            "gaps": json.dumps(self.gaps, ensure_ascii=False),
            "letter": self.letter,
            "cv_bullets": json.dumps(self.cv_bullets, ensure_ascii=False),
            "screening": json.dumps(self.screening, ensure_ascii=False),
            "first_seen": self.first_seen or now,
            "last_seen": now,
            "status": self.status,
            "funded": 1 if self.funded else 0,
            "funding_note": self.funding_note,
            "raw_text": self.raw_text,
            "source": self.source,
            "cv_personalized": self.cv_personalized,
        }


class Store:
    def __init__(self, path: str | Path = "offers.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        # tolerate concurrent writers (check_live.py vs deep_scan.py etc.)
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(_SCHEMA)
        # Migrate older schemas: ADD COLUMN is a no-op if the column exists,
        # so this is safe to run every startup as the schema evolves.
        for col, ddl in [("funded", "INTEGER NOT NULL DEFAULT 0"), ("funding_note", "TEXT"), ("source", "TEXT"), ("cv_personalized", "TEXT")]:
            try:
                self.conn.execute(f"ALTER TABLE offers ADD COLUMN {col} {ddl}")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def exists(self, oid: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM offers WHERE id=?", (oid,))
        return cur.fetchone() is not None

    def upsert(self, rec: OfferRecord) -> bool:
        """Insert or update. Returns True if this was a brand-new offer."""
        is_new = not self.exists(rec.id)
        row = rec.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO offers ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET "
            f"title=excluded.title, url=excluded.url, company=excluded.company, "
            f"location=excluded.location, remote=excluded.remote, city=excluded.city, "
            f"fit_score=excluded.fit_score, strengths=excluded.strengths, gaps=excluded.gaps, "
            f"letter=excluded.letter, cv_bullets=excluded.cv_bullets, screening=excluded.screening, "
            f"last_seen=excluded.last_seen, status=excluded.status, "
            f"funded=excluded.funded, funding_note=excluded.funding_note, raw_text=excluded.raw_text, source=excluded.source, cv_personalized=excluded.cv_personalized",
            tuple(row.values()),
        )
        self.conn.commit()
        return is_new

    def mark_seen(self, oid: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute("UPDATE offers SET last_seen=? WHERE id=?", (now, oid))
        self.conn.commit()

    def set_status(self, oid: str, status: str) -> None:
        self.conn.execute("UPDATE offers SET status=? WHERE id=?", (status, oid))
        self.conn.commit()

    def get(self, oid: str) -> OfferRecord | None:
        row = self.conn.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
        return self._from_row(row) if row else None

    def list_offers(
        self,
        remote_only: bool = False,
        status: str | None = None,
        min_fit: int = 0,
        funded_only: bool = False,
        since: str | None = None,
        limit: int = 200,
    ) -> list[OfferRecord]:
        conds: list[str] = []
        params: list = []
        if min_fit > 0:
            conds.append("fit_score >= ?")
            params.append(min_fit)
        if remote_only:
            conds.append("remote = 1")
        if funded_only:
            conds.append("funded = 1")
        if since:
            conds.append("first_seen >= ?")
            params.append(since)
        if status:
            conds.append("status = ?")
            params.append(status)
        q = "SELECT * FROM offers"
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY (fit_score IS NULL), fit_score DESC, first_seen DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(q, params).fetchall()
        return [self._from_row(r) for r in rows]

    def stats(self) -> dict:
        cur = self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(remote) AS remote, "
            "SUM(status='new') AS new, "
            "SUM(status='applied') AS applied, "
            "SUM(funded) AS funded, "
            "AVG(fit_score) AS avg_fit FROM offers"
        )
        r = cur.fetchone()
        return {
            "total": r["total"] or 0,
            "remote": r["remote"] or 0,
            "new": r["new"] or 0,
            "applied": r["applied"] or 0,
            "funded": r["funded"] or 0,
            "avg_fit": round(r["avg_fit"] or 0, 1),
        }

    def _from_row(self, r: sqlite3.Row) -> OfferRecord:
        def jload(v, default):
            try:
                return json.loads(v) if v else default
            except Exception:
                return default

        return OfferRecord(
            id=r["id"],
            title=r["title"],
            url=r["url"],
            company=r["company"],
            location=r["location"],
            remote=bool(r["remote"]),
            city=r["city"],
            fit_score=r["fit_score"],
            strengths=jload(r["strengths"], []),
            gaps=jload(r["gaps"], []),
            letter=r["letter"],
            cv_bullets=jload(r["cv_bullets"], []),
            screening=jload(r["screening"], {}),
            first_seen=r["first_seen"],
            last_seen=r["last_seen"],
            status=r["status"],
            funded=bool(r["funded"]),
            funding_note=r["funding_note"] or "",
            raw_text=r["raw_text"] or "",
            source=r["source"] or "",
            cv_personalized=r["cv_personalized"] or "",
        )

    def close(self) -> None:
        self.conn.close()
