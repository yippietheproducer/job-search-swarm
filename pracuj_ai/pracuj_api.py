"""pracuj.pl internal listing API — a deterministic, curl-reachable source.

WHY THIS EXISTS
---------------
For most of this project, pracuj.pl data required ego-browser: every `www.pracuj.pl` and
`it.pracuj.pl` page returns **403** to a bare HTTP client (Cloudflare bot-block — alive, not
dead). That made the whole board LLM/browser-bound and slow.

The board's own XHR endpoint is a different host and **does not block**:

    https://massachusetts.pracuj.pl/jobOffers/listing/grouped?ws=1&wm=home-office&subservice=1

It answers **HTTP 200 with JSON**, no browser, no cookies, no auth. Verified directly, and
the payload carries exactly the fields this project has been missing:

`workSchedules`       -> closes the known blind spot that `offers` had NO schedule column
`workModes`           -> "Praca zdalna" / "Praca stacjonarna" / "Praca hybrydowa"
`positionLevels`      -> "Młodszy specjalista (junior)" / "Starszy (senior)" / "Ekspert"
`jobDescription`      -> the FULL BODY, so hours can be parsed without a browser render
`expirationDate`      -> real liveness, instead of probing the offer page
`salaryDisplayText`   -> the ad's own salary string
`offers[]`            -> one entry per city, each with its own `partitionId` and URL

THE FACET IS PROVEN, NOT ASSUMED
--------------------------------
A filter is unproven until two different values return different result sets ("the Remotive
bug class" — a filter silently ignored looks identical to a filter that matched nothing):

    ws=1 (part-time)     -> offersCount 46
    ws=0 (NOT part-time) -> offersCount 2233

Two values, two result sets. `ws=1` is the board's real part-time facet.

THE 46-vs-3 MYSTERY, RESOLVED
-----------------------------
`it.pracuj.pl` advertises 46 part-time remote IT offers; an earlier lane harvested only 3,
and I wrongly called that a 15x gap with 43 unharvested offers. It is not a gap. 46 is the
**row** count across city duplicates; the same query returns `groupedOffersTotalCount: 28`
unique **roles**. Of those 28, 15 fail the senior gate and 9 fail the software gate, leaving
exactly 3 qualified roles. Both numbers were right; they count different things. Use
`grouped_offers_total_count` for roles and `offers_total_count` for rows, and never compare
one against the other.
"""
from __future__ import annotations

import json
import re
import urllib.request

from . import evidence as E

BASE = "https://massachusetts.pracuj.pl/jobOffers/listing"

# Facet values, all verified against the live endpoint.
PART_TIME = "ws=1"          # "część etatu"  -> 46 for IT+remote, 112 for IT alone, 539 all-PL
REMOTE = "wm=home-office"    # "praca zdalna"
IT_BOARD = "subservice=1"    # IT category (subservice=0 is the general board)

# CONTRACT TYPE. Discovered on 2026-09-22, and it is the same blind spot as justjoin's
# `b2b_contract`: the part-time facet (ws=1) shows 46 remote IT offers while the B2B category
# shows 2,209 - 88% of remote IT - because those are contract ENGAGEMENTS whose weekly load is
# not published. `tc` takes a NUMERIC value (`tc=b2b` raises HTTPError); the meanings below were
# established by reading the `typesOfContract` each value actually returns:
#   tc=1    6 offers, all types
#   tc=2  265 offers, Umowa zlecenie 71 / Kontrakt B2B 67 / Umowa o pracę 31
#   tc=3 2209 offers, Kontrakt B2B dominant        <- the B2B category
#   tc=4    5 offers, Umowa na zastępstwo
#   tc=7    4 offers, Umowa o staż / praktyki
CONTRACT_B2B = "tc=3"
CONTRACT_OTHER = "tc=2"

_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# The board writes these in Polish; map once, here, so no caller string-matches Polish.
_SCHEDULE_PART_TIME = "część etatu"
_LEVEL_JUNIOR = "młodszy"
_LEVEL_SENIOR = ("starszy", "ekspert", "ekspertka", "architekt", "kierownik", "menedżer")
_MODE_REMOTE = "zdalna"
_RE_FULL_TIME = re.compile(r"full[- ]?time|pełny etat|pelny etat|vollzeit", re.I)

# pracuj writes numbers with NBSP (\u00a0) and narrow NBSP inside them, e.g.
#   '200–250\u00a0zł netto (+\u00a0VAT)\u00a0/ godz.'
# A naive .replace(' ', '') leaves '20\xa0000' and raises ValueError on int(). This is the
# fourth units/formatting trap in this project and the second involving NBSP specifically.
_RE_NUM = re.compile(r"[^\d]")


def parse_hourly_rate(salary_text: str) -> tuple[int | None, int | None, str]:
    """Extract an hourly PLN band from pracuj's salary string. Returns (low, high, how).

    Only hourly rates are returned: a monthly figure for a contract role cannot be pro-rated to
    part-time without knowing the contracted weekly load, which these ads do not publish.
    """
    s = salary_text or ""
    if not re.search(r"(?:/\s*(?:h|godz)|za godzin|hourly)", s, re.I):
        return None, None, "not an hourly rate"
    nums = [_RE_NUM.sub("", x) for x in re.findall(r"[\d][\d\s\u00a0]{0,7}\d", s)]
    vals = [int(x) for x in nums if x.isdigit() and int(x) >= 10]
    if not vals:
        return None, None, "hourly but no parsable number"
    lo = min(vals)
    hi = max(vals) if len(vals) > 1 else None
    return lo, hi, f"{lo}" + (f"-{hi}" if hi else "") + " zł/h"


def listing_url(*, kind: str = "grouped", page: int = 1, per_page: int = 200,
                part_time: bool | None = None, remote: bool = False,
                it_only: bool | None = True, contract: str | None = None) -> str:
    """Build a listing URL. `kind` is "grouped" (roles, with offers[]) or "count".

    `contract` is a raw facet such as `CONTRACT_B2B` ("tc=3"). It exists because pracuj's
    contract category is 48x larger than its part-time facet and is where the remote contract
    work lives.
    """
    if kind not in ("grouped", "count"):
        raise ValueError(f"kind must be 'grouped' or 'count', got {kind!r}")
    if page < 1 or per_page < 1:
        raise ValueError("page and per_page must be >= 1")
    params = [f"pn={page}", f"rop={per_page}"]
    if part_time is not None:
        params.append("ws=1" if part_time else "ws=0")
    if remote:
        params.append("wm=home-office")
    if it_only is not None:
        params.append(f"subservice={1 if it_only else 0}")
    if contract:
        params.append(contract)
    return f"{BASE}/{kind}?" + "&".join(params)


def _default_get(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://it.pracuj.pl/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
        return json.loads(r.read().decode("utf-8", "replace"))


def count(*, get=None, **facets) -> int:
    """Cheapest possible question to the board: how many offers match these facets?"""
    get = get or _default_get
    data = get(listing_url(kind="count", per_page=50, **facets))
    return int(data.get("offersCount") or 0)


def level_of(position_levels) -> tuple[str, bool]:
    """Classify the board's own level chips. Returns (level, also_senior).

    The MOST JUNIOR tier present wins. An ad listing "Specjalista (mid) / Starszy (senior)"
    is open to a mid, so it is mid-eligible — and the senior tier is reported separately in
    `also_senior` so the ambition is visible rather than silently discarded.

    My first version had senior win outright. That rejected DATAIQ, the project's own #1
    pick, because its ad lists both mid and senior. The rule was stricter than the market.
    """
    blob = " ".join(position_levels or []).lower()
    senior_only = any(s in blob for s in _LEVEL_SENIOR)
    if _LEVEL_JUNIOR in blob:
        return "junior", senior_only
    # 'mid'/'regular' are unambiguous. 'specjalista' is not: it also appears inside
    # 'Starszy specjalista', so it only counts as mid when no senior marker is present.
    if "mid" in blob or "regular" in blob:
        return "mid", senior_only
    if "specjalista" in blob and not senior_only:
        return "mid", senior_only
    if senior_only:
        return "senior", True
    return "unknown", False


def body_verdict(body: str) -> tuple[str, str]:
    """Decide part-time status from the ad's own text field.

    IMPORTANT LIMIT, measured rather than assumed: the listing API's `jobDescription` is a
    **short summary, not the full ad body**. For APRIORIT's Intern C++ role it is 90
    characters ("Your responsibilities, Visiting lectures., Doing homework...."), and
    Monterail's genuine body text ("Availability: Full-time (B2B contract/hourly rates).")
    is **absent from this field entirely** — that sentence came from the rendered offer page.
    So this function can only ever be a *supporting* signal.

    What it can do reliably:
      - when it states explicit part-time hours, believe it (Strong Soft: "1/2 etatu" -> 20h)
      - when it explicitly asserts full-time, reject (its absence is what we cannot read)
      - when it is silent, return "unknown" — which callers must treat as "hours UNVERIFIED",
        not as "part-time confirmed". Only the chips can carry a row that far, and such rows
        need a browser render of the offer page before being sent.

    Explicit part-time HOURS beat a passing mention of full-time, because ads routinely
    describe a part-time start with a path to full-time: DATAIQ says "starts in a part-time
    capacity (~20 hours per week), with the potential to scale up to full-time commitment".
    A naive `full-time in body` test would have killed the best role in the set.
    """
    text = body or ""
    hours, hours_ev = E.parse_hours(text)
    if hours is not None and hours <= 30:
        return "part-time", f"text states {hours:g}h ({hours_ev})"
    hint = E.part_time_hint(text)
    if hint:
        return "part-time", f"part-time hint: {hint}"
    if _RE_FULL_TIME.search(text):
        return "full-time", "the ad text asserts full-time"
    return "unknown", "no hours in the text field (this field is a summary, not the body)"


def annotate(row: dict) -> dict:
    """Attach the hours verdict and, crucially, whether the row still needs verification.

    `needs_body_check` is the honest flag: the listing API cannot prove a row is part-time
    when its text field is silent, and for several rows it is. Those rows carry the board's
    `Część etatu` chip and nothing more, so they must be rendered in a browser before any
    application is sent.
    """
    verdict, why = body_verdict(row.get("body") or "")
    return {**row, "hours_verdict": verdict, "hours_reason": why,
            "needs_body_check": verdict == "unknown"}


def normalize(group: dict) -> list[dict]:
    """One row per CITY offer, each carrying its parent role's machine-readable facets.

    Returns [] for anything that is not a well-formed group, so one bad payload cannot
    crash a sweep of thousands.
    """
    if not isinstance(group, dict):
        return []
    title = str(group.get("jobTitle") or "").strip()
    company = str(group.get("companyName") or "").strip()
    schedules = [str(s) for s in (group.get("workSchedules") or [])]
    modes = [str(m) for m in (group.get("workModes") or [])]
    levels = [str(x) for x in (group.get("positionLevels") or [])]
    lvl, also_senior = level_of(levels)
    if not title:
        return []
    rows = []
    for o in (group.get("offers") or []):
        if not isinstance(o, dict):
            continue
        url = str(o.get("offerAbsoluteUri") or "").strip()
        if not url:
            continue
        rows.append({
            "title": title,
            "company": company,
            "url": url,
            "offer_id": o.get("partitionId"),
            "city": str(o.get("displayWorkplace") or ""),
            "is_whole_poland": bool(o.get("isWholePoland")),
            # machine-readable board facets — NOT inferred from prose
            "work_schedules": schedules,
            "work_modes": modes,
            "position_levels": levels,
            "is_part_time": any(_SCHEDULE_PART_TIME in s.lower() for s in schedules),
            "is_remote": any(_MODE_REMOTE in m.lower() for m in modes),
            "level": lvl,
            "also_senior": also_senior,
            "salary_text": str(group.get("salaryDisplayText") or ""),
            "contracts": [str(c) for c in (group.get("typesOfContract") or [])],
            "body": str(group.get("jobDescription") or ""),
            # WARNING: `body` is the listing's short summary, NOT the full ad body.
            # Measured at 90 chars for one real posting. Use it as a hint only; the full
            # text still requires the offer page (403 to curl -> ego-browser).
            "technologies": list(group.get("technologies") or []),
            "posted": group.get("initialPublicated") or group.get("lastPublicated"),
            "expires": group.get("expirationDate"),
            "group_id": group.get("groupId"),
            "source": "pracuj-api",
        })
    return rows


def sweep(*, get=None, max_pages: int = 10, per_page: int = 200, **facets) -> dict:
    """Page a grouped listing into normalized rows.

    Stops when a page returns nothing new, and reports both counts so a caller cannot
    confuse roles with rows (the mistake that produced the phantom 46-vs-3 gap).
    """
    get = get or _default_get
    rows: list[dict] = []
    seen: set[str] = set()
    meta = {"rows": 0, "roles": 0, "pages": 0,
            "offers_total_count": None, "grouped_offers_total_count": None}
    for page in range(1, max_pages + 1):
        data = get(listing_url(kind="grouped", page=page, per_page=per_page, **facets))
        meta["pages"] = page
        meta["offers_total_count"] = data.get("offersTotalCount",
                                              meta["offers_total_count"])
        meta["grouped_offers_total_count"] = data.get("groupedOffersTotalCount",
                                                      meta["grouped_offers_total_count"])
        groups = data.get("groupedOffers") or []
        if not groups:
            break
        new = 0
        for g in groups:
            for r in (annotate(x) for x in normalize(g)):
                key = str(r["url"])
                if key not in seen:
                    seen.add(key)
                    rows.append(r)
                    new += 1
        if new == 0:
            break
    meta["rows"] = len(rows)
    meta["roles"] = len({r["group_id"] for r in rows if r.get("group_id")}) or len(rows)
    return {"meta": meta, "rows": rows}


def qualify(row: dict) -> tuple[bool, str]:
    """The hard filters, applied to the board's own fields and the employer's own body.

    Order matters: the body check runs before the level check so the reason reported is the
    most decisive one.
    """
    if not row.get("is_part_time"):
        return False, "not part-time in workSchedules"
    # The employer's own prose overrides a permissive chip (see body_verdict).
    verdict, why = body_verdict(row.get("body") or "")
    if verdict == "full-time":
        return False, f"workSchedules says part-time but {why}"
    if not row.get("is_remote"):
        return False, "not remote in workModes"
    # Only an ad with NO junior/mid tier is out. "mid / senior" stays in (see level_of).
    if row.get("level") == "senior":
        return False, "senior/expert-only level chip"
    title = (row.get("title") or "").lower()
    dev = ("develop", "engineer", "programist", "fullstack", "full-stack", "frontend",
           "front-end", "backend", "back-end", "software", "python", "javascript",
           "typescript", "react", "tester", "test", "qa", "data scientist")
    nondev = ("product owner", "project manager", "pm ", "architect", "sap", "prepress",
              "audit", "cyber", "erp", "tutor", "interpreter", "seo", "fundraiser",
              # added after 'Team Lead' and the ITAM internship leaked through: both are
              # non-engineering even though their titles look technical.
              "lead", "manager", "asset management", "licen", "scrum", "owner",
              # 'Business Development' contains the dev keyword 'develop', so it passed the
              # dev test. Found by sweeping all 539 part-time remote Polish offers rather than
              # the 46 IT ones. Also seen live: a weather company's BD rep.
              "business development", "representative", "sales", "account exec",
              "doradca", "przedstawiciel", "handlow", "lektor", "asystent", "specjalista ds")
    if any(x in title for x in nondev):
        return False, "non-software title"
    if not any(x in title for x in dev):
        return False, "no software keyword in title"
    return True, "passes part-time + remote + level + software"
