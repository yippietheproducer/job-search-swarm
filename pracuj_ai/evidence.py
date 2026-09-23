"""Deterministic evidence scoring — the anti-gaming term.

Why this module exists
----------------------
`fit_score` (see `tailor.py::SYSTEM`) is an LLM judgement of how well a posting
matches the candidate. That is fine as an *opinion*, but it has a structural
weakness: generative judgement scores what is PRESENT and is not evidence that
anything is ABSENT. A 275-character stub saying "Junior Software Developer,
fully remote, flexible time, no degree required" therefore scores well, because
it presents as junior + remote + flexible with nothing to contradict it. The
ad-farm's copy is engineered to match exactly that objective.

`fit_score` alone also cannot be fixed by rewording the prompt, because the
failure is that a *classification under uncertainty* is being done by
*generative reasoning with no uncertainty term*.

This module supplies the missing term, and it is deliberately NOT an LLM call:
evidence is a count of which decision-relevant attributes are actually present
WITH a quotable substring. It cannot be gamed by keyword-stuffing, because a
stuffed post cannot manufacture an hours figure, a rate, or a real employer
domain without also becoming a real posting.

Three numbers, never one
------------------------
    skill_fit   — can he do the work?      (LLM opinion; may be generous)
    evidence    — how much is actually known?   (computed here; deterministic)
    eligibility — do the hard gates pass?  (computed here; part-time + remote + level)

The actionable decision combines them:

    actionable = eligibility.all_pass AND evidence >= 60

`is_thin()` in `scam_filter.py` was the embryo of this idea; this promotes it
from a caveat into a first-class score.
"""
from __future__ import annotations

import re

from .remote_truth import verify_remote

# ---------------------------------------------------------------------------
# Hours / FTE — the single most important attribute here, because "part-time
# only" is a hard requirement and boards are unreliable about it.
# ---------------------------------------------------------------------------

# A bare mention of "part-time" is NOT hours evidence: an ad that says
# "flexible ... (full-time, part-time, side-gig, or contract)" states no hours.
# Only a number counts.
_HOURS_PATTERNS: tuple[str, ...] = (
    r"(\d{1,3})\s*(?:\+\s*)?(?:h|hr|hrs|hrs\.|hours?)\s*(?:/|per\s+|a\s+)?\s*(?:week|wk|tyg\w*)",
    r"(\d{1,3})\s*(?:godzin\w*)\s*(?:/|na\s+|w\s+)?\s*(?:tydzie\w*|tyg\w*)",
    r"(?:week|wk|tyg\w*)\s*(?:of\s+)?(\d{1,3})\s*(?:h|hr|hrs|hours?)",
    r"(\d{1,3})\s*hrs?\b",
)
_FTE_PATTERNS: tuple[str, ...] = (
    r"(\d(?:[.,]\d+)?)\s*(?:fte|etatu)",
    r"(\d)\s*/\s*(\d)\s*etatu",  # "1/2 etatu" -> 20h
)
# Informational only — a part-time *signal* with no number. Recorded so a human
# can follow up, never counted as evidence.
_PART_TIME_HINT = re.compile(
    r"\b(part[- ]?time|część etatu|niepełny etat|half[- ]time|0[.,]5\s*fte)\b", re.I)

_MONTHLY_HOURS_PATTERNS: tuple[str, ...] = (
    r"(\d{1,3})\s*(?:[-\u2013\u2014]\s*(\d{1,3})\s*)?h(?:rs?)?\s*/\s*(?:msc|mies\.?|miesi\w*|month)",
    r"(\d{1,3})\s*(?:[-\u2013\u2014]\s*(\d{1,3})\s*)?(?:godzin\w*|godz\.?)\s*/\s*(?:mies\w*|msc|month)",
)
# Average weeks per month. Polish ads quote capacity per MONTH ("40-60h/msc"),
# so a weekly figure must be derived before the part-time cap can be applied.
_WEEKS_PER_MONTH = 4.33

# Domains that are MARKETPLACES, not employers. A board URL must never be
# credited as evidence of employer identity - otherwise every row on a board
# earns the attribute for free and the gate stops meaning anything.
_BOARD_DOMAINS: tuple[str, ...] = (
    "pracuj.pl", "justjoin.it", "nofluffjobs.com", "jobgether.com", "remotive.com",
    "linkedin.com", "indeed.com", "glassdoor.com", "arbeitnow.com", "hubmub.com",
    "nodesk.co", "workingnomads.com", "remoteok.com", "remoterocketship.com",
    "wellfound.com", "useme.com", "freelancermap.com", "theprotocol.it",
    "bulldogjob.pl", "rocketjobs.pl", "solid.jobs", "adzuna.com", "adzuna.pl",
    "jooble.org", "talent.com", "stepstone.de", "himalayas.app", "jobspresso.co",
    "remote3.co", "startup.jobs", "cryptojobslist.com", "jobicy.com",
)

_RATE_PATTERNS: tuple[str, ...] = (
    r"(?:[$€£]|PLN|EUR|USD|GBP)\s?\d[\d\s.,]*\s*(?:[-–—]\s*(?:[$€£]|PLN|EUR|USD|GBP)?\s?\d[\d\s.,]*)?\s*(?:/|per\s+)?\s*(?:h|hr|hour|godz\w*)",
    r"\d[\d\s.,]*\s*(?:zł|PLN)\s*(?:/|per\s+|za\s+)?\s*(?:h|hr|hour|godz\w*)",
    r"(?:[$€£]|PLN|EUR|USD)\s?\d[\d\s.,]*\s*(?:k|tys\.?)?\s*(?:/|per\s+)?\s*(?:month|mo|mies\w*)",
)
_LEVEL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("senior-only", r"\b(senior|sr\.?|principal|staff|lead|architect|head of)\b"),
    ("explicit-level", r"\b(junior|jr\.?|mid[- ]?level|entry[- ]level|"
                       r"graduate|trainee|intern|staż\w*|praktyk\w*|średniozaawansowan\w*)\b"),
)
_GEO_PATTERNS: tuple[str, ...] = (
    r"\b(worldwide|anywhere|global(?:ly)?)\b",
    r"\b(eu|europe|european union|emea|european)\b",
    r"\b(poland|polska|warsaw|warszawa|kraków|krakow|wrocław|wroclaw|gdańsk|gdansk|poznań|poznan)\b",
    r"\b(remote within|remote in|based in|located in)\b",
)
_STACK_TOKENS: tuple[str, ...] = (
    "python", "javascript", "typescript", "react", "next.js", "nextjs", "node", "node.js",
    "django", "flask", "fastapi", "sql", "postgres", "postgresql", "mysql", "redis", "docker",
    "kubernetes", "aws", "gcp", "azure", "terraform", "go", "golang", "rust", "java", "c#",
    "vue", "svelte", "tailwind", "graphql", "rest", "llm", "openai", "anthropic", "claude",
    "pytorch", "tensorflow", "pandas", "numpy", "git", "ci/cd", "elixir", "phoenix", "swift",
)

ATTRS: tuple[str, ...] = ("employer", "hours", "remote", "geo", "level", "stack", "rate")

_WS = re.compile(r"\s+")


def _clean(text: str | None) -> str:
    return _WS.sub(" ", (text or "")).strip()


def parse_hours(text: str | None) -> tuple[float | None, str | None]:
    """Return (baseline hours per week, evidence quote) or (None, None).

    BASELINE, not maximum — this is deliberate. A posting reading "20 hrs per
    week preferred, up to 40 hrs possible" commits to a 20-hour baseline with an
    option above it; taking the max would read 40 and wrongly fail a part-time
    cap. (That was a real bug: it rejected the Filen row.)

    Weekly-quantified claims ("20 hrs per week", "0.5 FTE", "1/2 etatu") are
    strong; a bare "40 hrs" is weak. Strong evidence wins when present.

    A bare mention of "part-time" is NOT hours evidence — an ad saying "flexible
    ... (full-time, part-time, side-gig, or contract)" states no hours. Only an
    explicit quantity counts; `part_time_hint()` reports the numberless signal.
    """
    body = _clean(text)
    if not body:
        return None, None

    strong: list[tuple[float, str]] = []
    weak: list[tuple[float, str]] = []

    # "1/2 etatu" first: the generic FTE regex would read the "1" as 1.0 FTE.
    for m in re.finditer(_FTE_PATTERNS[1], body, re.I):
        a, b = float(m.group(1)), float(m.group(2))
        if b:
            strong.append((40.0 * a / b, m.group(0).strip()))
    for m in re.finditer(_FTE_PATTERNS[0], body, re.I):
        fte = float(m.group(1).replace(",", "."))
        if 0 < fte <= 1.2:
            strong.append((40.0 * fte, m.group(0).strip()))

    # patterns 0-2 carry a week/wk/tyg unit -> strong; the last is bare -> weak
    for pattern in _HOURS_PATTERNS[:-1]:
        for m in re.finditer(pattern, body, re.I):
            hrs = float(m.group(1))
            if 1 <= hrs <= 80:
                strong.append((hrs, m.group(0).strip()))
    for m in re.finditer(_HOURS_PATTERNS[-1], body, re.I):
        hrs = float(m.group(1))
        if 1 <= hrs <= 80:
            weak.append((hrs, m.group(0).strip()))

    # Monthly-quoted capacity -> derive the weekly figure, or the part-time cap
    # can never be applied. "40-60h/msc" commits to 40h/month ~= 9.2h/week; the
    # lower bound is the baseline, consistent with the rule above.
    for pattern in _MONTHLY_HOURS_PATTERNS:
        for m in re.finditer(pattern, body, re.I):
            lo = float(m.group(1))
            if 1 <= lo <= 400:
                strong.append((lo / _WEEKS_PER_MONTH, m.group(0).strip()))

    pool = strong or weak
    if not pool:
        return None, None
    return min(pool, key=lambda t: t[0])


def part_time_hint(text: str | None) -> str | None:
    """A part-time *signal* without a number — informational, not evidence."""
    m = _PART_TIME_HINT.search(_clean(text))
    return m.group(0).strip() if m else None


def is_employer_domain(domain: str | None) -> bool:
    """True only for a plausible EMPLOYER-owned domain.

    Rejects job marketplaces and free hosts. Without this the attribute is
    credited on every board row for free - the gate would then be satisfied by
    the board itself rather than by a real employer.
    """
    if not domain:
        return False
    d = domain.lower().lstrip(".")
    if any(d == b or d.endswith("." + b) for b in _BOARD_DOMAINS):
        return False
    from .scam_filter import is_suspect  # local import: avoids import cycle
    return not is_suspect(f"https://{d}/", "", None)


def _find_quote(body: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        m = re.search(pattern, body, re.I)
        if m:
            return m.group(0).strip()
    return None


def detect(text: str | None, *, domain: str | None = None) -> dict[str, str | None]:
    """Per-attribute evidence quote, or None when the attribute is absent.

    `domain` is the employer domain when known. Employer identity is the one
    attribute that cannot be read from the text alone, so it is credited from a
    *resolvable, non-free-host* domain — the same signal `scam_filter` already
    computes. Without a domain, employer evidence stays uncredited (honest).
    """
    body = _clean(text)
    out: dict[str, str | None] = {a: None for a in ATTRS}

    if is_employer_domain(domain):
        out["employer"] = (domain or "").lower()
    # NOTE: a free-text company NAME is deliberately NOT accepted as employer
    # evidence. Measured, not assumed: crediting `offers.company` raised the mean
    # evidence of the 14 farm rows from 27.8 to 42.0 and erased the gap against
    # real rows (41.6) - because the farm names "HireSub"/"Workflowza"/"Flexgen"
    # too. A name any bot can type is not evidence. Only a resolvable
    # employer-owned DOMAIN is, and establishing that domain from a board posting
    # is exactly the one verification step the VERIFY_FIRST bucket exists for.

    _, hours_quote = parse_hours(body)
    out["hours"] = hours_quote

    verdict, remote_quote = verify_remote(body)
    out["remote"] = remote_quote if verdict == "REMOTE-CONFIRMED" else None

    out["geo"] = _find_quote(body, _GEO_PATTERNS)
    out["rate"] = _find_quote(body, _RATE_PATTERNS)

    # Level: an explicit level word counts; a senior-ONLY gate is a negative, not
    # evidence of a stated level, so it is left to `eligibility`.
    for kind, pattern in _LEVEL_PATTERNS:
        if kind == "explicit-level":
            out["level"] = _find_quote(body, (pattern,))
            break

    stack_hits = sorted({t for t in _STACK_TOKENS if re.search(rf"(?<![\w.]){re.escape(t)}(?![\w])", body, re.I)})
    out["stack"] = ", ".join(stack_hits[:6]) if stack_hits else None

    return out


def score_evidence(text: str | None, *, domain: str | None = None
                   ) -> tuple[int, dict[str, str | None], list[str]]:
    """Return (score 0-100, present{a:quote}, missing[a]).

    Deterministic and ungameable by phrasing: a stuffed stub cannot invent an
    hours figure, a rate, or a real domain without becoming a real posting.
    """
    present = detect(text, domain=domain)
    found = [a for a in ATTRS if present[a]]
    score = round(100 * len(found) / len(ATTRS))
    missing = [a for a in ATTRS if not present[a]]
    return score, present, missing


def eligibility(text: str | None, *, domain: str | None = None,
                max_hours: float = 30.0) -> dict:
    """Hard gates for THIS search: part-time, genuinely remote, not senior-only.

    `max_hours` defaults to 30 (<=0.75 FTE), matching the stated requirement.
    Gates return True/False plus the reason, and are NOT scores — a failed gate
    cannot be compensated by a high fit.
    """
    body = _clean(text)
    hours, hours_quote = parse_hours(body)
    verdict, remote_quote = verify_remote(body)

    senior_only = bool(re.search(_LEVEL_PATTERNS[0][1], body, re.I)) and not re.search(
        _LEVEL_PATTERNS[1][1], body, re.I)

    part_time = hours is not None and hours <= max_hours
    remote_ok = verdict == "REMOTE-CONFIRMED"

    reasons: list[str] = []
    if hours is None:
        reasons.append("no hours stated (part-time unproven)")
    elif hours > max_hours:
        reasons.append(f"hours {hours:g}/wk exceeds {max_hours:g}/wk cap")
    if not remote_ok:
        reasons.append(f"remote not confirmed (verdict={verdict})")
    if senior_only:
        reasons.append("senior-only gate")
    has_employer = is_employer_domain(domain)
    if not has_employer:
        reasons.append("employer domain unproven (board/free-host URL - verify the employer exists)")

    return {
        "part_time": part_time,
        "remote": remote_ok,
        "senior_ok": not senior_only,
        "hours_per_week": hours,
        "hours_quote": hours_quote,
        "remote_verdict": verdict,
        "remote_quote": remote_quote,
        "part_time_hint": part_time_hint(body),
        "has_employer": has_employer,
        "all_pass": bool(part_time and remote_ok and not senior_only and has_employer),
        "reasons": reasons,
    }


def actionable_verdict(skill_fit: int, evidence: int, elig: dict,
                       *, evidence_floor: int = 50, fit_floor: int = 60) -> str:
    """Bucket a row: SEND | VERIFY_FIRST | REJECT.

    `skill_fit` may be generous — that is exactly why it is not allowed to decide
    alone. Evidence and the hard gates do the gating. But `skill_fit` still has a
    FLOOR: gates and evidence can demote a row, they can never promote a poor
    match into SEND.

    Found on live data: without `fit_floor`, a senior role scoring fit=5 reached
    SEND merely by passing the hours/remote gates. Monotone by design — low fit
    always rejects; unproven gates or thin evidence escalate to a verification
    task; only fit AND gates AND evidence together give SEND.

    `evidence_floor` is a STARTING point, not a tuned number. Measured on real
    rows with a resolvable domain: a full posting ~86, a sparse-but-real ATS page
    ~57, the farm stub 29 (mean over all 14 farm rows 27.8, max 43). Calibrate
    against response data (ORCHESTRATION_DESIGN.md section 8) before trusting it.
    """
    if skill_fit < fit_floor:
        return "REJECT"
    if not elig.get("all_pass"):
        # A strong skill match with an unproven gate is a verification task, not
        # a rejection. This is the bucket the farm never escapes.
        return "VERIFY_FIRST"
    if evidence < evidence_floor:
        return "VERIFY_FIRST"
    return "SEND"
