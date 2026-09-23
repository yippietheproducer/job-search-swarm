"""Verify a `remote=1` claim against the POSTING BODY (not the search card).

Why this exists: `offers.db` sets `remote=1` from pracuj.pl search-card text
(`pracuj_ai/browser.py::_REMOTE_RE`) and never re-checks it against the posting.
`check_live.py` only issues HEAD requests, so it never sees the body either.

Lane 8 (2026-09-22) found the consequence: two rows scored `fit=95` with
`remote=1` whose own text says otherwise —

  * Eryk / Whitecoding — `"Location: Lagos, Nigeria"`, `"Approximately 80% of your
    work will be carried out from our service hub in Gbagada, Lagos"`.
  * CloudPlexo — `"The program uses a hybrid working setup, balancing focused
    in-office time with the flexibility to work remotely."`

For a candidate whose requirement is REMOTE ONLY, a false `remote=1` at fit 95 is
worse than a low score: it sends attention to an application that cannot be made.

This module is a pure function over text — no network, no DB — so it is safe to
call wherever a body is already available (`fetch_offer_text`), and trivially
testable. It returns the matched evidence so a human can audit the verdict.

## Measured limitation (do not skip this)

This checks the text you GIVE it. Verified 2026-09-22: running it against
`offers.db.raw_text` for the two contradicted rows returns `UNKNOWN`, because the
store keeps only ~400 characters of search-card text (Eryk 446c, CloudPlexo 393c)
— not the posting body. So this verifier is **useless unless it is fed a real
body**. Lane 8 reached its verdicts by fetching the pages itself.

Two consequences:
  1. Call it from the path that already fetches bodies (`fetch_offer_text` in
     `pracuj_ai/browser.py`), not from the store.
  2. `raw_text` should keep the full posting body, otherwise remoteness cannot be
     verified from the database at all — and 44% of scored rows already have
     <=500 chars, so the same thinness also degrades `fit_score`.
"""
from __future__ import annotations

import re

# Text that CONTRADICTS a remote claim (hybrid / onsite / other-geo hub).
CONFLICT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("HYBRID", r"\bhybrid\s+(?:working|work|setup|model|arrangement)\b"),
    ("HYBRID", r"\b(?:partially|partly|semi)[- ]remote\b"),
    ("ONSITE", r"\b\d{1,3}\s*%\s*(?:of\s+(?:your\s+)?work\s+)?(?:will\s+be\s+)?"
               r"(?:carried\s+out\s+)?(?:from|in)\s+(?:our|the)\b"),
    ("ONSITE", r"\b(?:on-?site|in-?office)\s+(?:position|role|work|days?)\b"),
    ("ONSITE", r"\bmust\s+(?:be\s+)?(?:able\s+to\s+)?(?:work\s+)?"
               r"(?:from\s+our|on-?site|in\s+the\s+office)\b"),
    ("ONSITE", r"\b\d\s+days?\s+(?:per\s+week\s+)?(?:on-?site|in\s+(?:the\s+)?office)\b"),
)

# Text that CONFIRMS a remote claim (used to avoid a false alarm).
CONFIRM_PATTERNS: tuple[str, ...] = (
    r"\bfully\s+remote\b", r"\b100\s*%\s*remote\b", r"\bremote[- ]first\b",
    r"\bremote\s*\(worldwide\)", r"\bwork\s+from\s+(?:anywhere|home)\b",
    r"\bzdaln\w*\b", r"\bw\s+pełni\s+zdaln\w*\b",
)

_WHITESPACE = re.compile(r"\s+")


def verify_remote(text: str | None) -> tuple[str, str]:
    """Classify a posting body: REMOTE-CONFIRMED | HYBRID | ONSITE | UNKNOWN.

    Returns (verdict, evidence). `evidence` is the matched substring for conflict
    verdicts, the confirming phrase for REMOTE-CONFIRMED, and "" for UNKNOWN.

    Order matters: an explicit conflict beats a generic "remote" mention, because
    "hybrid ... with the flexibility to work remotely" contains both.
    """
    body = _WHITESPACE.sub(" ", (text or "")).strip()
    if not body:
        return "UNKNOWN", ""

    conflict = None
    for kind, pattern in CONFLICT_PATTERNS:
        m = re.search(pattern, body, re.I)
        if m:
            conflict = (kind, m.group(0).strip())
            break

    confirm = None
    for pattern in CONFIRM_PATTERNS:
        m = re.search(pattern, body, re.I)
        if m:
            confirm = m.group(0).strip()
            break

    if conflict:
        return conflict[0], conflict[1]
    if confirm:
        return "REMOTE-CONFIRMED", confirm
    return "UNKNOWN", ""


def contradicts_remote(text: str | None) -> bool:
    """Convenience predicate: True when the body argues against `remote=1`."""
    return verify_remote(text)[0] in ("HYBRID", "ONSITE")
