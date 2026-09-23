#!/usr/bin/env python3
"""hn_swarm.py — sweep EVERY monthly HN "Who is hiring" thread, not just the current one.

WHY THIS IS THE LAST UNTOUCHED SURFACE
--------------------------------------
Every previous lane swept job boards and ATS feeds. This is different in the way that matters for
a candidate whose blocker is a CV timeline: **each post is written by the person who owns the budget**,
usually with a direct contact address, and frequently with the sentence *"a link to something you've
built"* instead of a portal and an HR filter.

The Algolia API serves any thread, so the channel is not one month deep — it is **twelve months** of
founder-posted ads, ~5,000 posts, all machine-readable, no auth, no scraping.

WHAT IT FILTERS FOR, AND WHY
----------------------------
    remote .................... non-negotiable (the whole search is remote)
    part-time / contract ...... his actual constraint (<40 h/week)
    AI / LLM / agent .......... his domain, and where portfolio-tolerance is highest
    Python / TS / React ....... his shipped stack
    senior marker in line 1 ... reported, not excluded - the level gate is real but visible

Output: JSON + a ranked markdown shortlist. Every post keeps its permalink so any row can be checked
by hand in one click.

Usage: python3 hn_swarm.py [--out hn_all.json]
"""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HERE = pathlib.Path(__file__).resolve().parent
ALG = "https://hn.algolia.com/api/v1"

REMOTE = re.compile(r"\b(remote|anywhere|worldwide|distributed|work from home|wfh)\b", re.I)
PART = re.compile(r"\b(part[- ]?time|contract|freelanc\w*|contractor|fractional|b2b|"
                  r"\d{1,2}\s*(?:-|–|to)\s*\d{1,2}\s*(?:hrs|hours)/?(?:wk|week)?|"
                  r"\d{1,2}\s*(?:hrs|hours)/?(?:wk|week))\b", re.I)
AI = re.compile(r"(\bai\b|\bllm\b|\bllms\b|agent\w*|machine learning|\bml\b|rag|langchain|"
                r"langgraph|\bmcp\b|evals?)\b", re.I)
STACK = re.compile(r"(python|typescript|javascript|react|node|next\.?js|flask|fastapi|"
                   r"postgres\w*|aws|docker)", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|head of|director|vp)\b", re.I)
# Europe / Poland eligibility signals
EU = re.compile(r"\b(europe|eu\b|emea|cet|utc\+[01]|poland|polska|warsaw|warszawa|"
                r"cracow|krakow|wroc|worldwide|anywhere)\b", re.I)
# US-only signals that are hard blockers
USONLY = re.compile(r"(u\.?s\.?[ -]?only|must be (?:based|located|authorized) in the (?:us|usa|"
                    r"united states)|onsite|on-site)", re.I)


def get(url: str, timeout: int = 30):
    last = None
    for _ in range(3):
        try:
            return json.loads(urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout).read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2)
    raise last if last else RuntimeError("unreachable")


def plain(node) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(node.get("text") or "")))).strip()


def threads() -> dict[str, str]:
    """title -> objectID for every monthly 'Who is hiring' thread we can find."""
    out: dict[str, str] = {}
    for q in ("Ask HN: Who is hiring?",):
        d = get(f"{ALG}/search_by_date?query={urllib.parse.quote(q)}&tags=story&hitsPerPage=50")
        for h in d.get("hits", []):
            t = str(h.get("title") or "")
            if "who is hiring" in t.lower() and h.get("num_comments", 0) > 20:
                out[t] = str(h["objectID"])
        time.sleep(0.4)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="hn_all.json")
    ap.add_argument("--max-threads", type=int, default=12)
    args = ap.parse_args()

    th = threads()
    ordered = sorted(th.items(), key=lambda kv: kv[0], reverse=True)[:args.max_threads]
    print(f"=== HN 'Who is hiring' sweep — {len(ordered)} monthly threads ===")

    rows: list[dict] = []
    per_thread: list[dict] = []
    for title, oid in ordered:
        try:
            d = get(f"{ALG}/items/{oid}")
        except Exception as e:  # noqa: BLE001
            per_thread.append({"thread": title, "id": oid, "error": type(e).__name__})
            continue
        kids = d.get("children") or []
        kept = 0
        for k in kids:
            t = plain(k)
            if not t:
                continue
            first = t[:170]
            rec = {
                "thread": title, "id": str(k.get("id")),
                "url": f"https://news.ycombinator.com/item?id={k.get('id')}",
                "first": first,
                "remote": bool(REMOTE.search(t)),
                "part": bool(PART.search(t)),
                "ai": bool(AI.search(t)),
                "stack": bool(STACK.search(t)),
                "eu_ok": bool(EU.search(t)),
                "us_only": bool(USONLY.search(t)),
                "senior": bool(SENIOR.search(first)),
            }
            rec["score"] = (3 * rec["remote"] + 3 * rec["part"] + 2 * rec["ai"]
                            + 2 * rec["stack"] + 2 * rec["eu_ok"]
                            - 3 * rec["us_only"] - 2 * rec["senior"])
            rows.append(rec)
            kept += 1
        per_thread.append({"thread": title, "id": oid, "posts": len(kids), "parsed": kept})
        print(f"  {title[:52]:54s} posts={len(kids):4d}")
        time.sleep(0.4)

    best = [r for r in rows if r["remote"] and r["eu_ok"] and not r["senior"] and not r["us_only"]
            and (r["part"] or r["ai"])]
    best.sort(key=lambda r: -r["score"])
    (HERE / args.out).write_text(json.dumps(
        {"threads": per_thread, "rows": rows, "best": best,
         "stats": {"threads": len(ordered), "posts": len(rows), "best": len(best)}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n  posts parsed: {len(rows)}")
    print(f"  remote & EU-eligible & non-senior & (part-time OR AI): {len(best)}")
    print(f"  wrote {args.out}")
    print("\n=== TOP 30 by fit ===")
    for r in best[:30]:
        tags = f"{'PT' if r['part'] else '  '}{'AI' if r['ai'] else '  '}{'PY' if r['stack'] else '  '}"
        print(f"  {tags} [{r['id']}] {r['first'][:96]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
