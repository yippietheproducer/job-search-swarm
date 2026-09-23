"""Monitor: periodically discover pracuj.pl offers and analyze new ones.

Reuses the browser (ego-browser) + tailor engine. Only *new* offers (by offer
ID) are fetched in full and scored by the LLM — seen offers are just touched
(``last_seen``) so we don't burn model calls re-analyzing the same listing.

Remote detection is client-side: pracuj.pl is an SPA that won't filter by URL,
so we read the offer's own text for "praca zdalna" / "hybrydowa" and flag it.
This is the human-in-the-loop monitor: it surfaces and ranks offers, you apply.
"""
from __future__ import annotations

import re
import sys
import time

from .browser import fetch_offer_text, search_offers
from .profile import load_profile
from .store import OfferRecord, Store, offer_id
from .tailor import analyze

_REMOTE_RE = re.compile(r"praca\s+zdalna|w\s+pełni\s+zdalna|100%\s*zdal|\bzdaln\w*|\bhybryd\w*", re.I)
_CITY_HINT_RE = re.compile(r"\b(Warszawa|Kraków|Wrocław|Gdańsk|Poznań|Łódź|Katowice|Gdynia|Sopot|Remote|Zdalnie)\b")


def detect_remote(text: str) -> bool:
    return bool(_REMOTE_RE.search(text or ""))


def detect_location(text: str) -> str:
    m = _CITY_HINT_RE.search(text or "")
    return m.group(1) if m else ""


def run_once(
    keyword: str,
    city: str | None,
    limit: int,
    profile_path: str,
    db_path: str = "offers.db",
    wait: int = 12,
    remote_filter: bool = False,
) -> dict:
    """Discover offers for a keyword and analyze any that are new. Returns counters."""
    profile = load_profile(profile_path)
    store = Store(db_path)
    offers = search_offers(keyword, city=city, limit=limit, remote=remote_filter)
    new_count = 0
    seen_count = 0
    for off in offers:
        oid = offer_id(off["url"])
        existing = store.get(oid) if store.exists(oid) else None
        if existing and existing.fit_score is not None:
            store.mark_seen(oid)
            seen_count += 1
            continue
        # New offer, or a previously-failed analysis (fit_score None) worth retrying.
        print(f"[monitor] {'NOWA' if not existing else 'PONOWNY scoring'} oferta: {off['title'][:55]}", file=sys.stderr, flush=True)
        try:
            text = fetch_offer_text(off["url"], wait=wait)
        except Exception as e:  # noqa: BLE001
            print(f"  ! błąd pobierania: {e}", file=sys.stderr)
            continue
        remote = detect_remote(text)
        try:
            res = analyze(profile, text)
        except Exception as e:  # noqa: BLE001
            print(f"  ! błąd analizy: {e}", file=sys.stderr)
            res = None
        rec = OfferRecord(
            id=oid,
            title=off["title"],
            url=off["url"],
            remote=remote,
            city=city,
            location=detect_location(text),
            fit_score=(res or {}).get("fit_score"),
            strengths=(res or {}).get("strengths", []) or [],
            gaps=(res or {}).get("gaps", []) or [],
            letter=(res or {}).get("list_motywacyjny", "") or "",
            cv_bullets=(res or {}).get("cv_bullets", []) or [],
            screening=(res or {}).get("screening", {}) or {},
            raw_text=text,
            first_seen=existing.first_seen if existing else "",
            status=existing.status if existing else "new",
        )
        if store.upsert(rec):
            new_count += 1
    store.close()
    summary = {"city": city, "discovered": len(offers), "new": new_count, "seen": seen_count}
    print(f"[monitor] cykl zakończony: {summary}", file=sys.stderr)
    return summary


def monitor_loop(
    keywords: list[str],
    city: str | None,
    limit: int,
    profile_path: str,
    db_path: str = "offers.db",
    interval_min: int = 30,
    max_cycles: int | None = None,
    remote_filter: bool = False,
) -> None:
    """Run ``run_once`` for every keyword, every ``interval_min`` minutes."""
    cycle = 0
    while True:
        cycle += 1
        for kw in keywords:
            tag = f"{kw}{('/' + city) if city else ''}{' [zdalne]' if remote_filter else ''}"
            print(f"[monitor] === cykl {cycle} ({tag}) ===", file=sys.stderr)
            try:
                run_once(kw, city, limit, profile_path, db_path, remote_filter=remote_filter)
            except Exception as e:  # noqa: BLE001
                print(f"[monitor] błąd cyklu: {e}", file=sys.stderr)
        if max_cycles and cycle >= max_cycles:
            break
        print(f"[monitor] czekam {interval_min} min...", file=sys.stderr)
        time.sleep(interval_min * 60)
