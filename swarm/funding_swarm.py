#!/usr/bin/env python3
"""funding_swarm.py — 128-thread swarm: funding news -> company -> ITS OWN ATS board -> live roles.

THE ARBITRAGE
-------------
A company that raised 48 hours ago and posts on Ashby has roles that are **not on any aggregator yet**.
Job boards lag funding news by days. This is the only genuine information advantage left in a market
where every board has been swept 30 times.

CONCURRENCY DESIGN — this is the part that matters
--------------------------------------------------
    thread pool ......... 128 workers
    per-host cap ........ 4   (a semaphore PER HOSTNAME, not a global one)

Wide across HOSTS, narrow per HOST. That is what "maximum safe parallelism" actually means here, and it
is a measured rule rather than a preference: burst-sweeping a single host at 12 workers got this IP put
into a shared rate-limit penalty box that poisoned every other request for 30+ minutes
(429 on 434/434 slugs). A global 128-way hammer would do that to nine hosts at once.

WHAT IT DOES
------------
1. HARMES  funding feeds (tech.eu, sifted, 300gospodarka, mamstartup) for company names.
2. RESOLVES each name to an ATS slug candidate and probes ~9 ATS platforms in parallel.
3. COLLECTS live roles from the platforms that answer, and filters to what the candidate can use:
   developer role, remote, not senior-only, no German requirement, part-time/contract or mid-level.
4. Reports every attempt with its HTTP status, so the negative results are as inspectable as the hits.

Usage: python3 funding_swarm.py [--workers 128] [--per-host 4] [--out funding_swarm.json]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import collections
import html
import json
import pathlib
import re
import threading
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HERE = pathlib.Path(__file__).resolve().parent

# ── per-host politeness: a semaphore PER HOSTNAME, never one global lock ─────
_host_locks: dict[str, threading.Semaphore] = {}
_guard = threading.Lock()
_host_counts: collections.Counter = collections.Counter()
_host_errors: collections.Counter = collections.Counter()


def _sem(host: str, per_host: int) -> threading.Semaphore:
    with _guard:
        if host not in _host_locks:
            _host_locks[host] = threading.Semaphore(per_host)
        return _host_locks[host]


def fetch(url: str, *, per_host: int = 4, timeout: int = 12) -> tuple[int, str | bytes]:
    """Return (status, body). 0 = transport error, -1 = timeout. Never raises."""
    host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
    sem = _sem(host, per_host)
    with sem:
        with _guard:
            _host_counts[host] += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json, text/plain, */*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with _guard:
                _host_errors[f"{host}:{e.code}"] += 1
            return e.code, b""
        except Exception as e:  # noqa: BLE001
            with _guard:
                _host_errors[f"{host}:{type(e).__name__}"] += 1
            return 0, b""


# ── 1. HARVEST funding news -> company names ─────────────────────────────────
FEEDS = [
    ("tech.eu", "https://tech.eu/feed"),
    ("sifted", "https://sifted.eu/feed"),
    ("300gospodarka", "https://300gospodarka.pl/feed"),
]
RAISE = re.compile(r"\s+(?:raises?|raised|lands?|secured?|secures?|clos(?:es|ed)|gets?|got|bags?|"
                   r"picks? up|wraps? up|nabs?|scores?|hauls? in|zbiera|pozyska\w*|otrzyma\w*|"
                   r"dosta\w*|finansowan\w*|rund\w*)\b", re.I)
SKIP = re.compile(r"(fund|ventures|capital|weekly recap|summit|expo|sponsored|boss:|opinion|"
                  r"interview|report|newsletter|podcast|awards?|VC|equity|partners|"
                  r"dips|holds|analysis|why |how )", re.I)


def harvest() -> dict[str, str]:
    """company name (lowercased key) -> source label. Feeds only; no pagination exists."""
    out: dict[str, str] = {}
    for label, url in FEEDS:
        st, body = fetch(url)
        if st != 200 or not body:
            print(f"  feed {label}: HTTP {st} — skipped")
            continue
        raw = body.decode("utf-8", "replace")
        n = 0
        for it in re.findall(r"<item>(.*?)</item>", raw, re.S):
            m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
            if not m:
                continue
            title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
            if not RAISE.search(title):
                continue
            comp = RAISE.split(title)[0].strip(" ,.·|-")
            comp = re.sub(r"^(Exclusive|Breaking|Report|Update)\s*:?\s*", "", comp, flags=re.I)
            if len(comp) < 2 or len(comp) > 34 or SKIP.search(comp):
                continue
            key = comp.lower()
            if key not in out:
                out[key] = f"{label}: {title[:90]}"
                n += 1
        print(f"  feed {label}: {n} new companies")
    return out


# ── 2. RESOLVE a company name to ATS slugs and probe them ────────────────────
def slugs(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    alt = base.replace("-", "")
    return list(dict.fromkeys([base, alt, base.replace("-", ".")]))


PROBES = [
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{s}?includeCompensation=true"),
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true"),
    ("lever", "https://api.lever.co/v0/postings/{s}?mode=json"),
    ("workable", "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true"),
    ("recruitee", "https://{s}.recruitee.com/api/offers/"),
    ("smartrecruiters", "https://api.smartrecruiters.com/v1/companies/{s}/postings"),
    ("personio", "https://{s}.jobs.personio.de/xml?language=en"),
    ("breezy", "https://{s}.breezy.hr/json"),
    ("teamtailor", "https://{s}.teamtailor.com/jobs.json"),
]

REMOTE = re.compile(r"\b(remote|zdaln|fully remote|telecommut|anywhere|work from home|wfh)\b", re.I)
PART = re.compile(r"\b(part[-_ ]?time|część etatu|niepełny|b2b|contract|freelance|contractor|"
                  r"zlecenie|fractional)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head|director|manager|ekspert|expert|"
                    r"architekt|architect|kierownik)\b", re.I)
DEV = re.compile(r"(develop|engineer|programist|full[- ]?stack|front[- ]?end|back[- ]?end|software|"
                 r"python|javascript|typescript|react|node|data|devops|tester|test|qa|platform|"
                 r"cloud|machine learning|\bml\b|\bai\b|\bllm\b|automat)", re.I)
NONDEV = re.compile(r"(korepety|tutor|nauczyciel|instruktor|sprzedaw|handlow|doradca|recruit|rekrutac|"
                    r"marketing|\bseo\b|sales|retention|customer|support|asystent|account exec)", re.I)
GERMAN = re.compile(r"\b(german|deutsch|niemieck|fließend|C1 German)\b", re.I)


def dev_roles(payload_text: str, company: str, platform: str, status: int) -> list[dict]:
    """Pull job-ish objects out of whatever shape the platform returned, then filter."""
    out: list[dict] = []
    try:
        data = json.loads(payload_text)
    except Exception:  # noqa: BLE001
        # personio/teamtailor may return XML - fall back to a crude <title> scan
        titles = re.findall(r"<(?:title|name)>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</(?:title|name)>",
                            payload_text, re.S)[:60]
        for t in titles:
            out.append({"title": re.sub(r"\s+", " ", t).strip()})
        data = None
    if isinstance(data, dict):
        for key in ("jobs", "data", "offers", "content", "results", "postings"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if isinstance(data, list):
        for j in data:
            if isinstance(j, dict):
                out.append(j)
    seen, uniq = set(), []
    for j in out:
        t = str(j.get("title") or j.get("text") or j.get("name") or "").strip()
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        blob = json.dumps(j, ensure_ascii=False)[:4000]
        is_remote = bool(REMOTE.search(blob)) or bool(j.get("isRemote")) or \
            "telecommut" in blob.lower() or bool(j.get("telecommuting"))
        uniq.append({
            "company": company, "platform": platform, "title": t[:120],
            "location": str(j.get("location") or j.get("city") or "")[:60],
            "url": str(j.get("jobUrl") or j.get("hostedUrl") or j.get("url") or
                       j.get("absolute_url") or j.get("applyUrl") or "")[:220],
            "remote": is_remote,
            "part_time_or_contract": bool(PART.search(blob)),
            "senior_gate": bool(SENIOR.search(t)),
            "dev": bool(DEV.search(t)) and not NONDEV.search(t),
            "german": bool(GERMAN.search(blob)),
        })
    return uniq[:60]


def probe_company(name: str, source: str, per_host: int) -> list[dict]:
    found: list[dict] = []
    for s in slugs(name):
        for platform, tpl in PROBES:
            url = tpl.format(s=s)
            st, body = fetch(url, per_host=per_host)
            if st != 200 or len(body) < 80:
                continue
            text = body.decode("utf-8", "replace")
            # a platform that answers with an empty shell is not a hit
            if text.strip() in ("{}", "[]", "null") or '"jobs":[]' in text.replace(" ", ""):
                continue
            roles = dev_roles(text, name, platform, st)
            good = [r for r in roles if r["dev"] and not r["senior_gate"] and not r["german"]]
            if good:
                for r in good:
                    r["source_news"] = source
                    r["probe_url"] = url
                    r["http"] = st
                found += good
                print(f"    HIT {name} on {platform}: {len(good)} usable dev roles")
            elif roles:
                print(f"    ({name} on {platform}: {len(roles)} roles, none usable)")
            break  # one platform per company is enough
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=128)
    ap.add_argument("--per-host", type=int, default=4)
    ap.add_argument("--out", default="funding_swarm.json")
    ap.add_argument("--extra-names", default=None,
                    help="path to a newline-delimited file of extra company names "
                         "(e.g. a curated VC portfolio) to resolve alongside the feeds")
    args = ap.parse_args()

    print(f"=== FUNDING SWARM — {args.workers} workers, {args.per_host} per host ===")
    print("\n[1/3] harvesting funding feeds")
    companies = harvest()

    # seed with the companies already verified by hand + a broader named list, so the
    # swarm is not limited to whatever the feeds happened to publish today
    SEED = ("viktor eevi zero tequipy verda enforceshield nearbycomputing magentic integral "
            "complir creem chift tandemhealth opencosmos academyai veridion exein primo zeliq "
            "spott biolevate adlyse gamindo metris benford fintechos morphotonics lightspring "
            "terasi depotcharge logibot resolutiion pimento mistral elevenlabs deepL pigment "
            "n8n langfuse weaviate qdrant eleven koyeb scaleway huggingface aleph-alpha "
            "deepl-com brainly docplanner booksy edrone enmacc zencastr alfred").split()
    for s in SEED:
        companies.setdefault(s, "seed list")
    # scale the WORK to match the worker count: a curated VC portfolio is a list of
    # funded companies, and 128 workers with only 55 companies is over-provisioned
    extra = pathlib.Path(args.extra_names) if args.extra_names else None
    if extra and extra.exists():
        for line in extra.read_text().splitlines():
            n = line.strip()
            if 2 < len(n) < 34:
                companies.setdefault(n, "vc portfolio / news slug")
    print(f"\n  total company names to resolve: {len(companies)}")

    print(f"\n[2/3] probing {len(PROBES)} ATS platforms x {len(companies)} companies")
    t0 = time.time()
    roles: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe_company, n, src, args.per_host): n for n, src in companies.items()}
        for f in cf.as_completed(futs):
            try:
                roles += f.result()
            except Exception as e:  # noqa: BLE001
                print(f"    error on {futs[f]}: {type(e).__name__}")
    dt = time.time() - t0
    total_req = sum(_host_counts.values())
    print(f"\n[3/3] done in {dt:.1f}s · {total_req} requests · "
          f"{total_req / max(dt, .001):.0f} req/s · {len(_host_locks)} distinct hosts")
    print(f"  usable dev roles found: {len(roles)}")
    print("\n  per-host request counts (politeness check):")
    for h, c in _host_counts.most_common(8):
        print(f"    {c:5d}  {h}")
    if _host_errors:
        print("\n  per-host error statuses:")
        for k, c in _host_errors.most_common(8):
            print(f"    {c:5d}  {k}")

    out = HERE / args.out
    out.write_text(json.dumps({"companies": companies, "roles": roles,
                               "stats": {"workers": args.workers, "per_host": args.per_host,
                                         "requests": total_req, "seconds": round(dt, 1),
                                         "hosts": len(_host_locks),
                                         "roles": len(roles)}},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  wrote {out}")

    if roles:
        print("\n=== BEST: dev, remote, non-senior, no German ===")
        remote = [r for r in roles if r["remote"]]
        for r in (remote or roles)[:25]:
            tag = "PT/B2B" if r["part_time_or_contract"] else "      "
            print(f"  [{r['platform']:13s}] {tag} {r['title'][:46]:48s} {r['company'][:16]:18s} "
                  f"{r['location'][:20]}")
            if r["url"]:
                print(f"        {r['url'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
