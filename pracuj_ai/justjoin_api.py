"""justjoin.it / rocketjobs.pl candidate API — the corrected multi-category sweep.

THE DEFECT THIS MODULE FIXES
----------------------------
The project spent ~20 research lanes querying `workingTimes=part_time` and concluded the remote
part-time market was "essentially empty". That was wrong, and the reason was a single category:

    part_time = 104 offers      <-- what every lane queried
    b2b_contract = 1,304        <-- where the work actually was
    freelance = 128
    (b2b_contract + remote = 649)

A role titled "Shopify Consultant (part-time)" carrying the prose "15-30 godzin tygodniowo" was
returned by `b2b_contract` and **never** by `part_time`. So a search for *part-time* work, phrased
in the user's own words, structurally could not see two thirds of the market.

Hence `CONTRACT_FACETS` below: **query the concept, not the label.** A part-time *seeker* on a B2B
contract negotiates hours; the label describes the contract *type*, not the weekly load.

THREE MEASURED TRAPS THIS MODULE ENCODES, SO THEY CANNOT RECUR
--------------------------------------------------------------
1. **UNITS.** `employmentTypes[]` carries BOTH `from`/`to` and `fromPerUnit`/`toPerUnit`, and the
   meaning of `fromPerUnit` depends on `unit`:

       unit='Hour'  -> fromPerUnit IS the hourly rate      (TQLO: 180.00 = 180 zl/h)
       unit='Month' -> fromPerUnit is the MONTHLY full-time rate (EduGO: 25000)

   Reading a monthly figure as hourly overstates by **168x**. Real observed output: "25,000-30,000
   zl/h" for an engineering manager. Never read `from`/`to` directly, and never ignore `unit`.

2. **PRO-RATING.** `unit='Month'` is a **full-time** monthly figure. Pay for h hours/week is
   `monthly * h / 40`, NOT `monthly`. A 18,000 zl/month contract at 20 h/week pays **9,000**.

3. **DUPLICATION.** The same role is emitted once per city. Measured duplicate factors: **17.0x**
   (nofluffjobs part-time) and 7.3x. Reporting row counts inflated a market by 17x once already.
   `dedupe()` collapses to distinct `(company, title)` and `sweep()` reports rows AND roles.

Also measured and encoded: `perPage` is **ignored** (always 10 rows/call — page with `from=`), and an
unfiltered/`full_time` query returns exactly `10000`, which is a **cap** and must be read as
">=10000", never as a count.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

JUSTJOIN = "https://justjoin.it"
ROCKETJOBS = "https://rocketjobs.pl"

# `workingTimes` facet values, all verified against the live API.
PART_TIME = "part_time"        # 104 offers
FULL_TIME = "full_time"        # 9,259 (capped)
B2B_CONTRACT = "b2b_contract"  # 1,304  <- invisible to a part_time query
FREELANCE = "freelance"        # 128
INTERNSHIP = "internship"      # 20

# The corrected query set: the work that can be done part-time, whatever it is called.
CONTRACT_FACETS = (PART_TIME, B2B_CONTRACT, FREELANCE)

# A total of exactly this value is a cap, not a count.
TOTAL_IS_CAPPED_AT = 10000

PAGE_SIZE = 10  # perPage is ignored by the API; this is the real page size
HOURS_PER_MONTH_FULL_TIME = 40 * 52 / 12  # 173.33

_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Non-engineering levels observed passing a rate-only filter and inflating a shortlist:
# 'Fractional CTO' (c_level), 'SAP RICE Programme Manager', 'Tech Lead (FinTech)'.
_NON_ENGINEERING_LEVELS = ("c_level", "manager", "senior", "expert", "lead", "head")

# The TITLE is authoritative when it names a level. Netguru's '(Senior) Frontend Developer with
# React' carries experienceLevel='mid', so a field-only check kept a senior ad. The reverse also
# happens, so both are checked and either can reject.
_RE_SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|lead|manager|head|principal|architect|cto|cio|director|dyrektor|"
    r"kierownik|ekspert|expert|owner)\b", re.I)

# Role-family filter. Without it the live sweep returned 155 'shortlist' rows including SAP
# consultants, a Fractional CTO and a QlikView BI expert - an inflated list is worse than a short one.
_RE_DEV_TITLE = re.compile(
    r"(develop|engineer|programist|full[- ]?stack|front[- ]?end|back[- ]?end|software|"
    r"python|javascript|typescript|react|node|next\.?js|tester|testing|\bqa\b|devops|"
    r"\bdata\b|\bai\b|\bml\b|\bllm\b|automat|cloud|platform|\bapi\b|integration)", re.I)
_RE_NONDEV_TITLE = re.compile(
    r"(korepety|tutor|nauczyciel|instruktor|lektor|sprzedaw|handlow|doradca|recruit|rekrutac|"
    r"marketing|\bseo\b|sales|retention|customer|support|asystent|assistant|"
    r"talent|payroll|księgow|kadry|opiekun)", re.I)

# Skills that are genuinely his. Used only to RANK, never to gate.
_STACK_KEYWORDS = ("python", "flask", "fastapi", "react", "next", "typescript", "javascript",
                   "node", "aws", "docker", "postgres", "sql", "redis", "playwright", "vitest",
                   "llm", "genai", "agent", "api", "rest", "tailwind", "git", "ci/cd")


def title_level_gate(row: dict) -> tuple[bool, str]:
    """Reject by the TITLE, which is authoritative when it names a level."""
    m = _RE_SENIOR_TITLE.search(str(row.get("title") or ""))
    if m:
        return False, f"title names a senior/non-engineering level: {m.group(0)!r}"
    return True, "title does not name a senior level"


def role_family(row: dict) -> tuple[str, str]:
    """Return (family, why) where family is 'dev', 'nondev' or 'unknown'."""
    title = str(row.get("title") or "")
    bad = _RE_NONDEV_TITLE.search(title)
    if bad:
        return "nondev", f"non-dev title marker: {bad.group(0)!r}"
    good = _RE_DEV_TITLE.search(title)
    if good:
        return "dev", f"dev title marker: {good.group(0)!r}"
    return "unknown", "no dev keyword in title"


def stack_fit(row: dict) -> int:
    """How many of his real skills the advert names. Ranking only - never a gate."""
    blob = " ".join([str(row.get("title") or "")] + [str(s) for s in (row.get("skills") or [])]).lower()
    return sum(1 for k in _STACK_KEYWORDS if k in blob)


def listing_url(facet: str = PART_TIME, *, from_: int = 0, remote: bool = True,
                levels: tuple[str, ...] = (), base: str = JUSTJOIN) -> str:
    """Build an offers URL. `levels` maps to `experienceLevels` (pipe-separated)."""
    if from_ < 0:
        raise ValueError("from_ must be >= 0")
    params = [f"workingTimes={facet}"]
    if remote:
        params.append("remoteWorkOptions=remote")
    if levels:
        params.append("experienceLevels=" + "|".join(levels))
    params.append(f"from={from_}")
    return f"{base}/api/candidate-api/offers?" + "&".join(params)


def _default_get(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed host)
        return json.loads(r.read().decode("utf-8", "replace"))


def is_capped(total) -> bool:
    """True when a total is the API's ceiling rather than a real count."""
    try:
        return int(total) >= TOTAL_IS_CAPPED_AT
    except (TypeError, ValueError):
        return False


def monthly_at(employment_types, hours_per_week: float) -> tuple[float | None, float | None, str]:
    """Unit-aware, pro-rated monthly pay for `hours_per_week`. Returns (low, high, how).

    Only B2B rows in native PLN are used — converted currencies are derived from the PLN one and
    would just be a rounding of the same number.
    """
    if hours_per_week <= 0:
        raise ValueError("hours_per_week must be > 0")
    b = next((c for c in (employment_types or [])
              if c.get("type") == "b2b" and c.get("currency") == "PLN"), None)
    if not b:
        return None, None, "no native PLN b2b rate"
    unit = str(b.get("unit") or "").lower()
    lo_raw, hi_raw = b.get("fromPerUnit"), b.get("toPerUnit")
    if lo_raw is None:
        return None, None, f"unit={unit!r} but no fromPerUnit"

    def _scale(v):
        if v is None:
            return None
        if unit == "hour":
            return float(v) * hours_per_week * 52 / 12
        if unit == "month":
            # a MONTH unit is a FULL-TIME figure; pro-rate by the hours actually worked
            return float(v) * hours_per_week / 40
        return None

    lo, hi = _scale(lo_raw), _scale(hi_raw)
    if lo is None:
        return None, None, f"unhandled unit {unit!r}"
    if unit == "hour":
        band = f"{lo_raw:g}" if hi_raw is None else f"{lo_raw:g}-{hi_raw:g}"
        return lo, hi, f"{band} zl/h"
    return lo, hi, f"{lo_raw:,.0f}/mo FT -> {lo:,.0f} at {hours_per_week:g}h"


def level_of(offer: dict) -> str:
    lvl = str(offer.get("experienceLevel") or "unknown").lower()
    return lvl if lvl else "unknown"


def languages_of(offer: dict) -> list[str]:
    return [str(l.get("code")) for l in (offer.get("languages") or []) if isinstance(l, dict)]


def requires_german(offer: dict) -> bool:
    return "de" in languages_of(offer)


def is_english_only(offer: dict) -> bool:
    return languages_of(offer) == ["en"]


def normalize(offer: dict, *, hours_per_week: float = 20.0) -> dict:
    """A flat row with the traps already handled. Never raises on a malformed offer."""
    if not isinstance(offer, dict):
        return {}
    lo, hi, how = monthly_at(offer.get("employmentTypes"), hours_per_week)
    row = {
        "title": str(offer.get("title") or "").strip(),
        "company": str(offer.get("companyName") or "").strip(),
        "slug": str(offer.get("slug") or offer.get("id") or ""),
        "url": f"{JUSTJOIN}/job-offer/{offer.get('slug')}" if offer.get("slug") else "",
        "level": level_of(offer),
        "working_time": str(offer.get("workingTime") or ""),
        "workplace": str(offer.get("workplaceType") or ""),
        "is_remote": str(offer.get("workplaceType") or "").lower() == "remote",
        "languages": languages_of(offer),
        "english_only": is_english_only(offer),
        "german_required": requires_german(offer),
        "skills": [str(s.get("name")) for s in (offer.get("requiredSkills") or [])
                   if isinstance(s, dict) and s.get("name")],
        "monthly_low": lo,
        "monthly_high": hi,
        "rate_how": how,
        "hours_assumed": hours_per_week,
        "expires": offer.get("expiredAt"),
        "published": offer.get("publishedAt"),
    }
    # role_family and stack_fit need the normalised shape, so they are attached after it exists.
    fam = role_family(row)
    row["role_family"], row["role_family_why"] = fam
    row["stack_fit"] = stack_fit(row)
    return row


def dedupe(rows: list[dict]) -> list[dict]:
    """Collapse to distinct (company, title). The same role is emitted once per city.

    Measured duplicate factors: 17.0x and 7.3x on real boards. Skipping this inflated a market
    estimate by 17x once already.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        k = (r.get("company", ""), r.get("title", ""))
        if not k[1] or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def sweep(*, facets: tuple[str, ...] = CONTRACT_FACETS, base: str = JUSTJOIN,
          get=None, hours_per_week: float = 20.0, max_pages: int = 90, sleep: float = 0.22,
          max_total: int = 2000) -> dict:
    """Page every facet, normalise, and report rows AND roles for each."""
    get = get or _default_get
    per_facet: dict[str, dict] = {}
    all_rows: list[dict] = []
    for facet in facets:
        rows: list[dict] = []
        seen_slugs: set[str] = set()
        paged = 0
        facet_error: str | None = None
        data: dict = {}  # bound even when the FIRST page fails, so the summary
        # below can never NameError on exactly the failure it exists to report
        for page in range(max_pages):
            url = listing_url(facet, from_=page * PAGE_SIZE, base=base)
            try:
                data = get(url)
            except Exception as e:  # noqa: BLE001 - a dead page ends the facet, it does not crash
                # MUST NOT be overwritten by the summary below: a facet that failed has to stay
                # visible as a failure, or it is indistinguishable from a facet that found
                # nothing - the silent-drop failure mode this whole project keeps hitting.
                facet_error = f"{type(e).__name__}: {str(e)[:100]}"
                break
            items = data.get("data") or []
            if not items:
                break
            for o in items:
                slug = str(o.get("slug") or o.get("id") or "")
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                r = normalize(o, hours_per_week=hours_per_week)
                if r:
                    rows.append(r)
            paged += 1
            if len(rows) >= max_total:
                break
            if sleep:
                time.sleep(sleep)
        roles = dedupe(rows)
        total = (data.get("meta") or {}).get("totalItems") if isinstance(data, dict) else None
        per_facet[facet] = {
            "error": facet_error,
            "ok": facet_error is None,
            "rows": len(rows), "roles": len(roles),
            "duplicate_factor": round(len(rows) / len(roles), 2) if roles else 0.0,
            "reported_total": total, "total_is_capped": is_capped(total), "pages": paged,
        }
        all_rows += roles
    roles = dedupe(all_rows)
    failures = [f for f, v in per_facet.items() if not v.get("ok")]
    return {"per_facet": per_facet, "rows": roles,
            "failed_facets": failures,
            "meta": {"roles": len(roles), "facets": len(facets),
                     "failed_facets": len(failures)}}


def qualify(row: dict, *, target_low: float = 9000.0, hours_per_week: float = 20.0) -> tuple[bool, str]:
    """The four gates, applied to normalised data. Senior-only rows are out; mid+senior stays in."""
    if row.get("german_required"):
        return False, "German required"
    if str(row.get("level") or "").lower() in _NON_ENGINEERING_LEVELS:
        return False, f"{row.get('level')}-only level field"
    ok, why = title_level_gate(row)
    if not ok:
        return False, why
    fam, fam_why = role_family(row)
    if fam != "dev":
        return False, fam_why
    if not row.get("is_remote"):
        return False, f"workplace={row.get('workplace')!r} (not remote)"
    if row.get("monthly_high") is None:
        return False, "no native PLN b2b rate published"
    if row["monthly_high"] < target_low:
        return False, (f"below target at {hours_per_week:g}h: top of band "
                       f"{row['monthly_high']:,.0f} < {target_low:,.0f}")
    return True, "passes remote + level + role + rate + language"


def shortlist(rows: list[dict], *, target_low: float = 9000.0,
              hours_per_week: float = 20.0) -> list[dict]:
    """Qualifying rows, ordered by pay then by how much of his real stack the ad names."""
    ok = [r for r in rows if qualify(r, target_low=target_low,
                                     hours_per_week=hours_per_week)[0]]
    return sorted(ok, key=lambda r: (-(r.get("monthly_low") or 0), -(r.get("stack_fit") or 0)))
