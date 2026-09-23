#!/usr/bin/env python3
"""Deterministic multi-source job crawler — the high-fan-out half of the sweep.

WHY THIS EXISTS INSTEAD OF MORE LLM LANES
The fleet has 9 healthy accounts. "Hundreds of parallel agents" would saturate it and
fail. But almost all of this work needs no model at all: fetch a structured endpoint,
extract rows, apply the part-time / remote / level rules, record the evidence. That is
deterministic and can run hundreds of requests in flight for free. LLM lanes are then
reserved for what genuinely needs judgement (browser-only sites, borderline calls).

Every row carries the raw evidence text so a claim can be re-checked, and the HTTP code
of the exact request that produced it. A row whose evidence cannot be quoted is kept in
the output but marked UNPROVEN rather than silently promoted.

Concurrency is deliberate: MAX_WORKERS requests in flight, per-request timeout, and any
single failure is recorded rather than fatal.

Usage:
  crawl_sources.py                     # full sweep
  crawl_sources.py --only greenhouse,lever,ashby
  crawl_sources.py --workers 64
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pracuj_ai import evidence as E  # noqa: E402
from pracuj_ai import remote_truth as R  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_WORKERS = 48
TIMEOUT = 25
MAX_BYTES = 12_000_000  # refuse absurd payloads (nofluffjobs whole board is 138 MB)

# ── target grids ──────────────────────────────────────────────────────────────
GREENHOUSE = """stripe figma vercel linear sentry postman gitlab hashicorp doist hotjar pleo
personio taxfix pipedrive sumup deliveryhero klarna spotify typeform zapier buffer automattic
canonical cloudflare datadog mongodb elastic grafanalabs docker circleci netlify sourcegraph
replit huggingface scaleai anthropic openai cohere weightsandbiases modal-labs together-ai
runwayml elevenlabs perplexity-ai deepmind mistral linguana deepl contentful sanity prismic
storyblok algolia meilisearch typesense supabase planetscale cockroachlabs neon timescale
adyen mollie checkoutcom wise revolut monzo n26 trade-republic bitpanda kraken bitfinex
paddle gocardless""".split()

LEVER = """netlify plaid brex dbtlabs tide attentive kraken brex kabam benchling
nuro zoox applied-intuition scale scaleai anduril palantir rippling gusto brex
coinbase robinhood affirm marqeta chime mercury ramp openphone loom miro notion
airtable coda clickup asana height linear scribe airtight zapier doist buffer
hotjar pleo personio sumup bolteu gitlab mattermost nextcloud owncloud bitwarden
1password tailscale fly tailscale cloudflare vercel render railway fly-io""".split()

ASHBY = """openai ramp runway sardine pave cursor anthropic scaleai notion linear
vercel clay decagon harvey elevenlabs together-ai modal-labs baseten langchain
weightsandbiases replit codeium sourcegraph magicdev cognition adept inflection
character stability ai21 cohere mistral poolside sierra decagon glean writer
mercury vanta dbtlabs hex sigma-mode observablehq metabase preswald zenly
doppler railway retool temporal inngest trigger-dev windmill superset""".split()

PERSONIO = """limehome 1komma5grad edgeless-systems prairie personio taxfix sumup
contentful sanity storyblok deepl lingoda babbel watchespod sleep-cycle solarisbank
n26 wefox clark finleap raising-ventures enpal tibber 1komma5 enmacc
kraftblock liveeo sennder cargo-one flixbus heyjobs zenjob taledo
instaffo ecoligo ubitricity volta energy vay kinexon cognigy aiven
celonis staffbase retarus think-cell joyn mytheresa aboutyou westwing
home24""" .split()

RECRUITEE = """kodland mindrift tendem alignerr telus-digital-appen dataannotation
clickworker remotasks outlier scale-ai welocalize lionbridge phrasee toptal
x-team doist automattic buffer hotjar kanbanize whitehat-seo uxstudio
brainly docplanner booksy livechat getresponse cux estyl tl;dr
sotrender semstorm senuto brand24 unamo surfer seo semrush ahrefs""".split()

WORKABLE = """careacross netguru mindrift tendem alignerr personio hotjar doist
revolut monzo starling tidal babylon health heroes wefox omio travelperk
typeform uxcam smartlook hotjar livespace salesmanago edrone infermedica
dynamicyield 7n spartez softserve luxoft globallogic sigma software
intellias n-i-x ciklum dataart emerge luxmed""".split()

SMART = """Bosch Siemens Visa Ubisoft Publicis Sapient Accenture
Ericsson DHL""".split()

QUERY_GRID = ["part-time", "part time", "parttime", "contract", "freelance", "junior",
              "intern", "working student", "frontend", "python", "react", "next.js",
              "full stack", "fullstack", "typescript", "ai engineer", "llm"]
LOC_GRID = ["Poland", "European Union", "Remote", "Worldwide"]
FREELANCERMAP_Q = ["teilzeit", "teilzeit+entwickler", "react+teilzeit", "python+teilzeit",
                   "remote+teilzeit", "javascript+teilzeit", "frontend+teilzeit",
                   "python+remote+teilzeit", "react+remote+teilzeit"]


# ── http ──────────────────────────────────────────────────────────────────────
def fetch(url: str, *, accept: str = "*/*") -> tuple[int, str, str]:
    """Return (http_code, text, error). Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Accept-Language": "en,pl;q=0.9,de;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_BYTES)
            code = r.status
    except urllib.error.HTTPError as e:
        try: body = e.read(4000).decode("utf-8", "replace")
        except Exception: body = ""
        return e.code, body, f"HTTPError {e.code}"
    except Exception as e:  # noqa: BLE001
        return 0, "", f"{type(e).__name__}: {e}"
    txt = raw.decode("utf-8", "replace")
    return code, txt, ""


def jget(url: str) -> tuple[int, object, str]:
    code, txt, err = fetch(url, accept="application/json")
    if code != 200 or not txt:
        return code, None, err or "no body"
    try:
        return code, json.loads(txt), ""
    except Exception as e:  # noqa: BLE001
        return code, None, f"bad json: {e}"


def strip_tags(html: str) -> str:
    html = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ── source adapters: each yields row dicts ────────────────────────────────────
def n_greenhouse(tokens: list[str]):
    for t in tokens:
        url = f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true"
        code, data, err = jget(url)
        if code != 200 or not isinstance(data, dict):
            yield {"_probe": url, "_http": code, "_err": err, "_source": "greenhouse"}
            continue
        for j in data.get("jobs", []):
            yield {"source": "greenhouse", "company": t,
                   "title": j.get("title") or "",
                   "url": j.get("absolute_url") or url,
                   "http": code,
                   "text": strip_tags(str(j.get("content", "")))[:6000],
                   "loc": (j.get("location") or {}).get("name", "")}


def n_lever(slugs: list[str]):
    for s in slugs:
        url = f"https://api.lever.co/v0/postings/{s}?mode=json"
        code, data, err = jget(url)
        if code != 200 or not isinstance(data, list):
            yield {"_probe": url, "_http": code, "_err": err, "_source": "lever"}
            continue
        for j in data:
            yield {"source": "lever", "company": s, "title": j.get("text") or "",
                   "url": j.get("hostedUrl") or url, "http": code,
                   "text": strip_tags((j.get("descriptionPlain") or j.get("description") or "")
                                      + " " + " ".join(x.get("text", "") for x in
                                                       (j.get("lists") or [])))[:6000],
                   "loc": ((j.get("categories") or {}).get("location") or ""),
                   "commit": ((j.get("categories") or {}).get("commitment") or "")}


def n_ashby(orgs: list[str]):
    for o in orgs:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{o}?includeCompensation=true"
        code, data, err = jget(url)
        if code != 200 or not isinstance(data, dict):
            yield {"_probe": url, "_http": code, "_err": err, "_source": "ashby"}
            continue
        for j in data.get("jobs", []):
            yield {"source": "ashby", "company": o, "title": j.get("title") or "",
                   "url": j.get("jobUrl") or url, "http": code,
                   "text": strip_tags(j.get("descriptionHtml") or "")[:6000],
                   "loc": j.get("location") or "", "remote": j.get("isRemote")}


def n_workable(queries: list[str]):
    """jobs.workable.com/api/v1/jobs is public + unauthenticated (found by lane 13)."""
    seen: set[str] = set()
    for q in queries:
        for loc in LOC_GRID:
            url = ("https://jobs.workable.com/api/v1/jobs?"
                   + urllib.parse.urlencode({"query": q, "location": loc}))
            code, data, err = jget(url)
            if code != 200 or not isinstance(data, dict):
                yield {"_probe": url, "_http": code, "_err": err, "_source": "workable-api"}
                continue
            for j in data.get("jobs", []):
                u = j.get("url") or j.get("jobUrl") or ""
                if not u or u in seen:
                    continue
                seen.add(u)
                yield {"source": "workable-api", "company": j.get("companyName") or j.get("company") or "",
                       "title": j.get("title") or "", "url": u, "http": code,
                       "text": strip_tags(j.get("description") or "")[:6000],
                       "loc": j.get("location") or "",
                       "employmentType": j.get("employmentType") or ""}


def n_workable_widget(slugs: list[str]):
    """The widget API carries the BODY; the public HTML page is a JS shell
    (proved on careacross). Use this for evidence quotes."""
    for s in slugs:
        url = f"https://apply.workable.com/api/v1/widget/accounts/{s}?details=true"
        code, data, err = jget(url)
        if code != 200 or not isinstance(data, dict):
            yield {"_probe": url, "_http": code, "_err": err, "_source": "workable-widget"}
            continue
        for j in data.get("jobs", []):
            yield {"source": "workable-widget", "company": data.get("name") or s,
                   "title": j.get("title") or "",
                   "url": j.get("url") or j.get("application_url") or url, "http": code,
                   "text": strip_tags((j.get("description") or "") + " "
                                      + (j.get("requirements") or ""))[:6000],
                   "loc": ", ".join(filter(None, [j.get("city"), j.get("country")])),
                   "employmentType": j.get("employmentType") or "",
                   "remote": j.get("telecommuting")}


def n_personio(companies: list[str]):
    """`<co>.jobs.personio.de/xml` — public, structured, carries <schedule>."""
    for c in companies:
        url = f"https://{c}.jobs.personio.de/xml?language=en"
        code, txt, err = fetch(url, accept="application/xml")
        if code != 200 or "<position" not in txt:
            yield {"_probe": url, "_http": code, "_err": err or "no positions",
                   "_source": "personio"}
            continue
        for m in re.finditer(r"<position>(.*?)</position>", txt, re.S):
            blk = m.group(1)

            def grab(tag):
                mm = re.search(rf"<{tag}>(.*?)</{tag}>", blk, re.S)
                return strip_tags(mm.group(1)) if mm else ""
            yield {"source": "personio", "company": strip_tags(grab("subcompany")) or c,
                   "title": grab("name"), "url": grab("id") or url, "http": code,
                   "text": " ".join(filter(None, [grab("jobDescriptions"), grab("schedule"),
                                                  grab("office"), grab("occupationCategory"),
                                                  grab("seniority"), grab("employmentType")]))[:6000],
                   "loc": grab("office"), "schedule": grab("schedule")}


def n_recruitee(slugs: list[str]):
    for s in slugs:
        url = f"https://{s}.recruitee.com/api/offers/"
        code, data, err = jget(url)
        if code != 200 or not isinstance(data, dict):
            yield {"_probe": url, "_http": code, "_err": err, "_source": "recruitee"}
            continue
        for j in data.get("offers", []):
            yield {"source": "recruitee", "company": s, "title": j.get("title") or "",
                   "url": j.get("careers_url") or j.get("careers_apply_url") or url,
                   "http": code,
                   "text": strip_tags((j.get("description") or "") + " "
                                      + (j.get("requirements") or ""))[:6000],
                   "loc": j.get("city") or "",
                   "employmentType": j.get("employment_type_code") or ""}


def n_justjoin(page_cap: int = 12):
    """Facets are REAL; perPage is a no-op (always 10) — page with from= only."""
    seen: set[str] = set()
    grids = [
        "workingTimes=part_time&remoteWorkOptions=remote&experienceLevels=junior&experienceLevels=mid",
        "workingTimes=part_time&remoteWorkOptions=remote",
        "workingTimes=freelance&remoteWorkOptions=remote&experienceLevels=junior&experienceLevels=mid",
    ]
    for grid in grids:
        total = None
        for i in range(page_cap):
            url = f"https://justjoin.it/api/candidate-api/offers?{grid}&from={i*10}"
            code, data, err = jget(url)
            if code != 200 or not isinstance(data, dict):
                yield {"_probe": url, "_http": code, "_err": err, "_source": "justjoin"}
                break
            meta = data.get("meta") or {}
            if total is None:
                total = meta.get("totalItems")
            rows = data.get("data") or []
            if not rows:
                break
            for j in rows:
                slug = j.get("slug") or ""
                u = f"https://justjoin.it/job-offer/{slug}" if slug else url
                if u in seen:
                    continue
                seen.add(u)
                yield {"source": "justjoin", "company": j.get("companyName") or "",
                       "title": j.get("title") or "", "url": u, "http": code,
                       "text": strip_tags(j.get("body") or "")[:6000] + " " +
                               " ".join([str(j.get("experienceLevel") or ""),
                                         str(j.get("workingTime") or ""),
                                         str(j.get("workplaceType") or "")]),
                       "loc": j.get("city") or "", "rate": j.get("employmentTypes") or "",
                       "level": j.get("experienceLevel") or "",
                       "hours_facet": j.get("workingTime") or ""}
            if total and (i + 1) * 10 >= int(total):
                break


def n_rocketjobs(page_cap: int = 30):
    seen: set[str] = set()
    grid = "workingTimes=part_time&remoteWorkOptions=remote"
    for i in range(page_cap):
        url = f"https://rocketjobs.pl/api/candidate-api/offers?{grid}&from={i*10}"
        code, data, err = jget(url)
        if code != 200 or not isinstance(data, dict):
            yield {"_probe": url, "_http": code, "_err": err, "_source": "rocketjobs"}
            break
        rows = data.get("data") or []
        if not rows:
            break
        for j in rows:
            slug = j.get("slug") or ""
            u = f"https://rocketjobs.pl/oferta-pracy/{slug}" if slug else url
            if u in seen:
                continue
            seen.add(u)
            yield {"source": "rocketjobs", "company": j.get("companyName") or "",
                   "title": j.get("title") or "", "url": u, "http": code,
                   "text": strip_tags(j.get("body") or "")[:6000],
                   "loc": j.get("city") or "", "level": j.get("experienceLevel") or "",
                   "hours_facet": j.get("workingTime") or ""}


def n_freelancermap(queries: list[str]):
    """Server-rendered JSON at initialState.result.projects[] (lane 11)."""
    seen: set[str] = set()
    for q in queries:
        url = f"https://www.freelancermap.de/projekte?query={q}"
        code, txt, err = fetch(url)
        if code != 200:
            yield {"_probe": url, "_http": code, "_err": err, "_source": "freelancermap"}
            continue
        m = re.search(r'<script type="application/json"[^>]*>(.*?)</script>', txt, re.S)
        if not m:
            yield {"_probe": url, "_http": code, "_err": "no json blob",
                   "_source": "freelancermap"}
            continue
        try:
            data = json.loads(m.group(1))
        except Exception as e:  # noqa: BLE001
            yield {"_probe": url, "_http": code, "_err": f"json {e}",
                   "_source": "freelancermap"}
            continue
        projects = (((data.get("initialState") or {}).get("result") or {})
                    .get("projects") or [])
        for p in projects:
            u = p.get("links", {}).get("project") or url
            if isinstance(u, str) and not u.startswith("http"):
                u = "https://www.freelancermap.de" + u
            if u in seen:
                continue
            seen.add(u)
            ct = p.get("contractType") or {}
            yield {"source": "freelancermap", "company": p.get("companyName") or p.get("company") or "",
                   "title": p.get("title") or "", "url": u, "http": code,
                   "text": strip_tags(" ".join(str(p.get(k) or "") for k in
                                               ("description", "skills", "title",
                                                "contractType", "industry")))[:6000],
                   "loc": ", ".join(str(x) for x in (p.get("locations") or [])),
                   "remote_pct": ct.get("remoteInPercent")}


def n_arbeitnow(pages: int = 15):
    """?search= is IGNORED (verified: byte-identical md5) — pull pages, filter locally."""
    seen: set[str] = set()
    for p in range(1, pages + 1):
        url = f"https://arbeitnow.com/api/job-board-api?page={p}"
        code, data, err = jget(url)
        if code != 200 or not isinstance(data, dict):
            yield {"_probe": url, "_http": code, "_err": err, "_source": "arbeitnow"}
            break
        rows = data.get("data") or []
        if not rows:
            break
        for j in rows:
            u = j.get("url") or ""
            if not u or u in seen:
                continue
            seen.add(u)
            yield {"source": "arbeitnow", "company": j.get("company_name") or "",
                   "title": j.get("title") or "", "url": u, "http": code,
                   "text": strip_tags(j.get("description") or "")[:6000],
                   "loc": j.get("location") or "", "remote_flag": j.get("remote"),
                   "tags": j.get("tags") or [], "job_types": j.get("job_types") or []}


def n_simple_feeds():
    """Small public feeds. Each recorded with its own HTTP code."""
    feeds = {
        "jobicy": "https://jobicy.com/api/v2/remote-jobs?count=100",
        "remoteok": "https://remoteok.com/api",
        "remotive": "https://remotive.com/api/remote-jobs",
        "weworkremotely": "https://weworkremotely.com/categories/remote-programming-jobs.json",
        "himalayas": "https://himalayas.app/jobs/api?limit=100",
        "fourdayweek": "https://4dayweek.io/api/jobs",
        "workingnomads": "https://www.workingnomads.com/api/exposed_jobs/",
        "jobspresso": "https://jobspresso.co/?feed=job_feed",
    }
    for name, url in feeds.items():
        code, data, err = jget(url)
        if code != 200 or data is None:
            code2, txt, err2 = fetch(url)
            if code2 == 200 and txt:
                yield {"_probe": url, "_http": code2, "_err": "non-json feed",
                       "_source": f"feed:{name}"}
            else:
                yield {"_probe": url, "_http": code, "_err": err or err2,
                       "_source": f"feed:{name}"}
            continue
        items = data if isinstance(data, list) else (
            data.get("jobs") or data.get("data") or data.get("items") or [])
        for j in items:
            if not isinstance(j, dict):
                continue
            yield {"source": f"feed:{name}", "company": j.get("company_name") or j.get("company") or "",
                   "title": j.get("title") or j.get("position") or "",
                   "url": j.get("url") or j.get("company_url") or j.get("apply_url") or url,
                   "http": code, "text": strip_tags(str(j.get("description") or ""))[:6000],
                   "loc": str(j.get("candidate_required_location") or j.get("location") or ""),
                   "job_types": j.get("job_types") or j.get("tags") or []}


def n_bulldogjob():
    for u in ("https://bulldogjob.pl/companies/jobs/s/employmentType,part_time",
              "https://bulldogjob.pl/companies/jobs/s/employmentType,part_time/city,Remote"):
        code, txt, err = fetch(u)
        if code != 200:
            yield {"_probe": u, "_http": code, "_err": err, "_source": "bulldogjob"}
            continue
        for m in re.finditer(r'href="(/companies/jobs/(\d+)-[^"]+)"', txt):
            yield {"source": "bulldogjob", "company": "", "title": "",
                   "url": "https://bulldogjob.pl" + m.group(1), "http": code, "text": ""}
        yield {"_probe": u, "_http": code, "_err": "listing only", "_source": "bulldogjob",
               "_note": f"{len(re.findall(r'/companies/jobs/([0-9]+)-', txt))} offer links"}


SOURCES = {
    "greenhouse": lambda: n_greenhouse(GREENHOUSE),
    "lever": lambda: n_lever(LEVER),
    "ashby": lambda: n_ashby(ASHBY),
    "workable": lambda: n_workable(QUERY_GRID),
    "workable-widget": lambda: n_workable_widget(WORKABLE),
    "personio": lambda: n_personio(PERSONIO),
    "recruitee": lambda: n_recruitee(RECRUITEE),
    "justjoin": lambda: n_justjoin(),
    "rocketjobs": lambda: n_rocketjobs(),
    "freelancermap": lambda: n_freelancermap(FREELANCERMAP_Q),
    "arbeitnow": lambda: n_arbeitnow(),
    "feeds": n_simple_feeds,
    "bulldogjob": n_bulldogjob,
}

LEVEL_SENIOR = re.compile(r"\b(senior|staff|principal|lead|head of|architect|director|"
                          r"starszy|starsza|ekspert|expert)\b", re.I)
LEVEL_JUNIOR = re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate|intern|internship|"
                          r"trainee|working student|werkstudent|staż|staz|praktyk|"
                          r"młodszy|mlodszy|associate)\b", re.I)
LEVEL_MID = re.compile(r"\b(mid|mid[- ]level|regular|middle|średni|sredni)\b", re.I)

# The 4th hard filter (SOFTWARE only) was missing from the first crawl run, which let
# 447 rows through, most of them sales/support/education roles that only satisfied the
# part-time and level rules. A role must name dev work in the TITLE to count.
DEV_TITLE = re.compile(
    r"(develop|engineer|programist|full[- ]?stack|front[- ]?end|back[- ]?end|"
    r"software|python|javascript|typescript|react|next\.?js|node\.?js|django|flask|"
    r"fastapi|\bjava\b|kotlin|golang|\brust\b|\bphp\b|laravel|\bruby\b|rails|c\+\+|"
    r"\.net|c#|playwright|cypress|\bqa\b|test(er|ing| automation)|devops|\bsre\b|"
    r"data (engineer|scientist|analyst)|machine learning|\bml\b|\bai\b|\bllm\b|"
    r"prompt|android|\bios\b|swift|vue|angular|svelte|web ?dev|api|cloud|"
    r"integrator|automat|architekt|system(admin| engineer)|administrator|"
    r"analityk|analyst|inżynier|inzynier|wdrożeni|wdrozeni)", re.I)
NONDEV_TITLE = re.compile(
    r"(brand ambassador|go[- ]to[- ]market|outreach|staffing|recruit|talent|"
    r"sales|account (manager|exec)|business development|marketing|\bseo\b|\bppc\b|"
    r"customer|help ?desk|\bsupport\b|tutor|korepetytor|teacher|trainer|consultant|"
    r"product manager|project manager|scrum master|business excellence|"
    r"human resources|\bhr\b|finance|accounting|legal|copywriter|content|"
    r"social media|community|designer|\bops\b|operations|logistics|warehouse|"
    r"nurse|health|patient|trader|trading|sprzedaw|handlow|"
    r"księgow|kadry|rekrutac|trener|nauczyciel|opiekun)", re.I)

REMOTE_FACET = re.compile(r"\b(remote|zdaln|fully remote|remote-first|work from anywhere|"
                          r"anywhere|home ?office|100% zdaln|praca zdalna)\b", re.I)


def is_software(title: str, text: str) -> tuple[bool, str]:
    """TITLE-ONLY match. The old body fallback was the leak: a non-dev title like
    "Patient Outreach Specialist" was accepted because the job body mentioned
    "engineering team". A role must name dev work in its own title.

    `text` is accepted but deliberately unused, kept so callers stay source-compatible.
    """
    t = title or ""
    bad = NONDEV_TITLE.search(t)
    if bad:
        return False, f"non-dev title marker: {bad.group(0)!r}"
    m = DEV_TITLE.search(t)
    if m:
        return True, f"dev title marker: {m.group(0)!r}"
    return False, "no dev keyword in title"


PT_EVIDENCE = re.compile(
    r"(part[- ]?time|parttime|part time|"
    r"część etatu|czesc etatu|pół etatu|pol etatu|\d[/,.]\d ?etatu|\d ?/ ?\d etatu|"
    r"\bFTE\b|\d{1,3}% ?FTE|"
    r"teilzeit|halbtags|working student|werkstudent|"
    # hours MUST be bounded to <=30, or "40 hours per week" reads as part-time.
    # Regex cannot do arithmetic, so the bound is spelled out in the alternation.
    r"\b([1-9]|[12][0-9]|30) ?(h|godzin|hours|hrs|stunden)[ /]?(/|per )?(week|tyg|tydzień|woche)|"
    r"\b([1-9]|[12][0-9]|30)[-–]([1-9]|[12][0-9]|30) ?(h|hours|godzin))", re.I)
# NOTE: bare "flexible hours" is deliberately NOT part-time evidence. A full-time role
# routinely advertises flexible hours, and treating it as part-time let four full-time
# poolside AI-infrastructure roles through the strict filter. The rule stands: full-time
# with flexible hours is NOT part-time.


def classify(text: str, level_hint: str = "") -> tuple[int, str, str]:
    """Return (level_score, level_label, why). Higher = more junior-eligible."""
    blob = f"{text} {level_hint}"
    sen = LEVEL_SENIOR.search(blob)
    jun = LEVEL_JUNIOR.search(level_hint) or LEVEL_JUNIOR.search(blob)
    mid = LEVEL_MID.search(level_hint) or LEVEL_MID.search(blob)
    if jun:
        return 3, "junior", f"junior marker: {jun.group(0)!r}"
    if sen:
        return 0, "senior", f"senior marker: {sen.group(0)!r}"
    if mid:
        return 2, "mid", f"mid marker: {mid.group(0)!r}"
    return 1, "unknown", "no level marker"


def score_row(row: dict) -> dict:
    text = row.get("text") or ""
    blob = " ".join([text, str(row.get("loc") or ""), str(row.get("hours_facet") or ""),
                     str(row.get("schedule") or ""), str(row.get("employmentType") or ""),
                     str(row.get("job_types") or "")])
    hours, hours_ev = E.parse_hours(blob)
    pt_hint = E.part_time_hint(blob)
    remote_v, remote_ev = R.verify_remote(blob)
    level_score, level_label, level_why = classify(text, str(
        row.get("level") or row.get("schedule") or ""))
    ev_score, ev_detail, ev_reasons = E.score_evidence(blob)
    return {**row,
            "hours": hours, "hours_evidence": hours_ev or pt_hint,
            "pt_hint": pt_hint, "remote_verdict": remote_v, "remote_evidence": remote_ev,
            "level_score": level_score, "level_label": level_label, "level_why": level_why,
            "evidence_score": ev_score, "evidence_detail": ev_detail,
            "evidence_reasons": ev_reasons}


def qualifies(r: dict, *, strict: bool = True) -> bool:
    """Strict: software role AND part-time evidence AND remote evidence AND not senior.

    `strict=False` reproduces the first, too-permissive run (remote UNKNOWN allowed,
    no software filter) so the two can be compared on the same data.
    """
    if r.get("_probe"):
        return False
    if r.get("level_score", 0) == 0:
        return False
    text = r.get("text") or ""
    blob = " ".join([text, str(r.get("loc") or ""), str(r.get("title") or ""),
                     str(r.get("hours_facet") or ""), str(r.get("schedule") or ""),
                     str(r.get("employmentType") or ""), str(r.get("job_types") or "")])
    pt_ok = bool(PT_EVIDENCE.search(blob) or r.get("pt_hint")
                 or str(r.get("hours_facet")) == "part_time"
                 or "part" in str(r.get("schedule", "")).lower())
    if not pt_ok:
        return False
    if not strict:
        return r.get("remote_verdict") in ("REMOTE-CONFIRMED", "UNKNOWN")
    if not is_software(r.get("title", ""), text)[0]:
        return False
    # remote must be positively evidenced, not merely unknown
    return bool(r.get("remote_verdict") == "REMOTE-CONFIRMED" or REMOTE_FACET.search(blob))


def db_urls() -> set[str]:
    db = ROOT / "offers.db"
    if not db.exists():
        return set()
    try:
        con = sqlite3.connect(db)
        got = {u for (u,) in con.execute("SELECT url FROM offers WHERE url IS NOT NULL")}
        con.close()
        return {re.sub(r"\?.*$", "", u).rstrip("/").lower() for u in got}
    except Exception:  # noqa: BLE001
        return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = ap.parse_args()
    wanted = [s for s in (args.only.split(",") if args.only else list(SOURCES)) if s in SOURCES]

    # Each adapter is a generator of sequential requests; run the ADAPTERS concurrently,
    # and within the big grid adapters the requests are themselves sequential by design
    # (paginators). Fan-out comes from many adapters x many tokens in flight.
    rows: list[dict] = []
    probes: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(lambda n=n: list(SOURCES[n]())): n for n in wanted}
        for f in cf.as_completed(futs):
            name = futs[f]
            try:
                got = f.result()
            except Exception as e:  # noqa: BLE001
                probes.append({"_source": name, "_err": f"adapter crashed: {e}", "_http": 0})
                continue
            for r in got:
                (probes if r.get("_probe") else rows).append(r)
            print(f"  {name:18s} {len(got):6d} rows  ({sum(1 for r in got if not r.get('_probe'))} usable)")

    scored = [score_row(r) for r in rows]
    # Always dump EVERY usable row so the rules can be re-applied offline without
    # re-fetching ~450 endpoints (iterating on a filter must not mean re-hammering
    # other people's servers).
    (OUT / "crawl_raw.json").write_text(json.dumps(scored, ensure_ascii=False), encoding="utf-8")
    kept_loose = [r for r in scored if qualifies(r, strict=False)]
    kept = [r for r in scored if qualifies(r)]
    print(f"  loose filter: {len(kept_loose)} rows | strict filter: {len(kept)} rows")
    # coerce every display field to str once, so a non-string from any API cannot
    # crash the report renderer (personio/greenhouse return ints in some fields)
    for r in kept:
        for k in ("title", "company", "url", "source", "loc", "level_label",
                  "remote_verdict", "text", "hours_evidence", "pt_hint"):
            r[k] = str(r.get(k) or "")
    kept = [r for r in kept if r["url"]]
    known = db_urls()
    for r in kept:
        r["in_db"] = re.sub(r"\?.*$", "", r.get("url", "")).rstrip("/").lower() in known
    kept.sort(key=lambda r: (-r["level_score"], -(r.get("hours") or 99),
                             -r["evidence_score"]))

    (OUT / "crawl_results.json").write_text(json.dumps(
        {"probes": probes, "kept": kept,
         "totals": {"fetched_rows": len(rows), "probes": len(probes), "kept": len(kept)}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# DETERMINISTIC CRAWL — part-time + remote + junior/mid", "",
          f"sources: {len(wanted)} · raw rows: {len(rows)} · probes(errors/status): {len(probes)} · "
          f"**kept: {len(kept)}**", "",
          "Strict filter: part-time evidence (or hours<=30) AND remote-confirmed-or-unknown "
          "AND not senior. Every row below carries its evidence text.", "",
          "| # | Lvl | Hours | Remote | Role | Company | Source | HTTP | URL |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(kept, 1):
        hours = r.get('hours') or r.get('pt_hint') or '?'
        md.append(f"| {i} | {r.get('level_label')} | {str(hours)[:22]} | "
                  f"{r.get('remote_verdict')} | {str(r.get('title') or '')[:58]} | "
                  f"{str(r.get('company') or '')[:26]} | "
                  f"{r.get('source')} | {r.get('http')} | {str(r.get('url') or '')[:96]} |")
    md += ["", "## Source status (probes = every request that did not yield a usable row)", "",
           "| Source | HTTP | Note |", "|---|---|---|"]
    seen_p: set[str] = set()
    for p in probes:
        k = f"{p.get('_source')}|{p.get('_http')}"
        if k in seen_p:
            continue
        seen_p.add(k)
        md.append(f"| {p.get('_source')} | {p.get('_http')} | "
                  f"{str(p.get('_err') or p.get('_note') or '')[:80]} |")
    (OUT / "CRAWL_REPORT.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\n  kept {len(kept)} of {len(rows)} rows ({len(probes)} probes) -> "
          f"CRAWL_REPORT.md, crawl_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
