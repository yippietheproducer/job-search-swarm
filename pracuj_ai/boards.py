"""Discovery from public remote-job JSON APIs — no browser required.

A *different* discovery channel from pracuj.pl SPA scraping: structured JSON
from global remote-job boards. Fast, no ego, fully concurrent with ego-based
scans. Many roles are EU/Poland-friendly (remote-ok candidate).

Sources (all public, no auth):
  - https://remotive.com/api/remote-jobs      (NO server-side filter — see note)
  - https://remoteok.com/api                   (full JSON, filtered client-side)
  - https://www.arbeitnow.com/api/job-board-api

Upstream note (verified 2026-09-22 by hashing the returned job-id set for five
different URLs — `?search=part-time`, `?search=react`, `?category=software-dev`,
`?limit=3` and no params — all five returned the SAME 18 job ids):
**Remotive ignores `search`, `category` and `limit` entirely** and only ever
exposes its latest ~18 postings. It is therefore treated as a *term-agnostic*
source: fetched once and filtered client-side via `relevant()`. Sending the
params would be harmless but would only multiply identical requests.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
import urllib.request

SOURCES = ["remotive", "remoteok", "arbeitnow", "solidjobs"]
_UA = {"User-Agent": "pracuj-ai/0.1 (+job discovery)"}


def _get_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _strip_tags(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _oid(url: str, fallback: str) -> str:
    base = (url or fallback or "x").encode("utf-8")
    return "b" + hashlib.md5(base).hexdigest()[:16]


def _norm(url, title, company, location, tags, description, source) -> dict:
    return {
        "id": _oid(url, title or source),
        "title": (title or "").strip(),
        "url": url,
        "company": (company or "").strip(),
        "location": (location or "remote").strip(),
        "remote": True,
        "tags": tags or [],
        "description": _strip_tags(description or ""),
        "source": source,
    }


def fetch_remotive(term: str, limit: int = 20) -> list[dict]:
    """Latest Remotive postings, optionally narrowed to `term` CLIENT-SIDE.

    Remotive ignores `search`/`category`/`limit` (see module docstring), so the
    request is sent without them and the filter is applied here. Callers that
    sweep many keywords should pass `term=""` once instead of once per keyword
    (`fetch_all` already does this via `_TERM_AGNOSTIC`).
    """
    try:
        data = _get_json("https://remotive.com/api/remote-jobs")
    except Exception:
        return []
    out = []
    needle = (term or "").strip().lower()
    for j in data.get("jobs", []):
        rec = _norm(
            j.get("url"), j.get("title"), j.get("company_name"),
            j.get("candidate_required_location"), j.get("tags"),
            j.get("description"), "remotive")
        if needle:
            hay = " ".join([
                rec["title"], rec["company"], " ".join(rec["tags"]),
                rec["description"], str(j.get("category") or ""),
            ]).lower()
            if needle not in hay:
                continue
        out.append(rec)
    return out[:limit] if limit else out


def fetch_remoteok(term: str, limit: int = 60) -> list[dict]:
    try:
        data = _get_json("https://remoteok.com/api")
    except Exception:
        return []
    out = []
    for j in data:
        if not isinstance(j, dict) or "legal" in j or "position" not in j:
            continue  # skip the legal-notice object
        out.append(_norm(
            j.get("url"), j.get("position"), j.get("company"),
            j.get("location"), j.get("tags"),
            j.get("description"), "remoteok"))
    return out


def fetch_arbeitnow(term: str, limit: int = 60) -> list[dict]:
    try:
        data = _get_json("https://www.arbeitnow.com/api/job-board-api")
    except Exception:
        return []
    out = []
    for j in data.get("data", []):
        out.append(_norm(
            j.get("url"), j.get("title"), j.get("company_name"),
            j.get("office_location") or j.get("location"), j.get("tags"),
            j.get("description"), "arbeitnow"))
    return out


def fetch_solidjobs(term: str = "", limit: int = 50) -> list[dict]:
    """SOLID.Jobs public API — polskie oferty IT z gwarantowanymi widełkami.

    Junior-only by default (search.experiences=Junior); `term` jako searchTerm.
    Docs: https://github.com/solid-company/solid-jobs-client
    """
    params = {
        "campaign": "pracuj-ai-bot",
        "search.categories": "Developer",
        "search.experiences": "Junior",
        "pageSize": min(limit, 500),
        "sortActive": "validFrom",
        "sortDirection": "desc",
    }
    if term:
        params["search.searchTerm"] = term
    url = ("https://solid.jobs/public-api/offers/IT?"
           + urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"X-Api-Version": "1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    out = []
    for j in data.get("jobs", []):
        sal = j.get("salary") or {}
        loc = ", ".join(j.get("locations") or []) or ("remote" if j.get("isRemote") else "")
        desc_bits = []
        if sal.get("from"):
            desc_bits.append(f"Widełki: {int(sal['from'])}-{int(sal.get('to') or 0)} PLN {sal.get('employmentType','')}")
        for s in (j.get("skills") or []):
            desc_bits.append(f"{s.get('name')} ({s.get('level')})")
        if j.get("languages"):
            desc_bits.append("Języki: " + ", ".join(
                f"{l.get('name')} {l.get('level')}" for l in j["languages"]))
        desc_bits.append(j.get("description") or "")
        out.append(_norm(
            j.get("url"), j.get("title"), j.get("company"),
            loc, [s.get("name") for s in (j.get("skills") or [])],
            "\n".join(desc_bits), "solidjobs"))
    return out


_FETCHERS = {
    "remotive": fetch_remotive,
    "solidjobs": fetch_solidjobs,
    "remoteok": fetch_remoteok,
    "arbeitnow": fetch_arbeitnow,
}

# Sources that ignore the query term upstream, so fetching them once per keyword
# is pure waste (Remotive returned an identical payload for 5 different URLs).
# These are called ONCE with term="" and filtered client-side by relevant().
_TERM_AGNOSTIC = {"remotive"}


def relevant(offer: dict, keywords: list[str]) -> bool:
    text = (offer["title"] + " " + " ".join(offer.get("tags", [])) + " " +
            offer.get("description", "")).lower()
    return any(k.lower() in text for k in keywords)


def fetch_all(keywords: list[str], sources: list[str] | None = None,
              limit: int = 20) -> list[dict]:
    """Fetch + keyword-filter offers across the selected boards."""
    sources = sources or SOURCES
    seen: set[str] = set()
    out: list[dict] = []
    for src in sources:
        fetcher = _FETCHERS.get(src)
        if not fetcher:
            continue
        for kw in ([ "" ] if src in _TERM_AGNOSTIC else keywords):
            try:
                offers = fetcher(kw, limit=limit)
            except Exception:
                offers = []
            for o in offers:
                if o["url"] in seen:
                    continue
                seen.add(o["url"])
                if relevant(o, keywords):
                    out.append(o)
    return out
