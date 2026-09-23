"""pracuj.pl browser integration via ego-browser (governed, human-in-the-loop).

This module drives a REAL browser through `ego-browser` (the workspace's
governed browser substrate). It is used for READ-ONLY discovery: open a
pracuj.pl city page, extract live offer links + descriptions. No applications
are sent — applying stays a human decision (see apply.py).

Why ego-browser:
- It reuses real, logged-in Chrome profiles when imported, so pracuj.pl's
  Cloudflare challenge is bypassed (a fresh headless browser would be blocked).
- The `ego` JS API gives us task spaces + accessibility snapshots, far cheaper
  (token-wise) than raw HTML.

Keyword filtering: pracuj.pl is a SPA and its URL-based keyword filtering is
unreliable, so we fetch a city page and filter offers client-side by keyword
(substring match on the offer title). This is robust and transparent.

Note on profile: viewing offers needs no login, so the default "Your ego"
profile works. Applying later requires importing your Chrome profile
(`ego-browser import --browser chrome --profile "Profile 2"`).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

EGO_BIN = "ego-browser"
DEFAULT_PROFILE = "Default"  # e.g. 'Profile 2' must be imported for login

_OFFER_RE = re.compile(r"url=(https://www\.pracuj\.pl/praca/[^)\s]*,oferta,\d+[^)\s]*)")
_TEXT_RE = re.compile(r'text "(.*?)"')
# Work-mode signal present in the search-result card text (so we can flag
# remote/hybrid offers without opening each detail page).
_REMOTE_RE = re.compile(r"praca\s+zdalna|w\s+pe\u0142ni\s+zdalna|100%\s*zdal|\bzdaln\w*|\bhybryd\w*", re.I)


def _clean_snapshot(txt: str) -> str:
    """Extract only the human-visible text from an accessibility snapshot.

    The raw snapshot is full of `ref=`/`url=` markup that confuses the tailor
    model. Keeping just the `text "..."` lines yields clean, readable job
    content (titles, responsibilities, requirements) with minimal noise.
    """
    lines = [m.group(1) for m in _TEXT_RE.finditer(txt)]
    return "\n".join(lines)


def _cleanup() -> None:
    """Legacy browser reset hook.

    Historically this killed ALL `ego-browser` processes via `pkill -f
    'ego-browser'`. That was unsafe: it also killed *concurrent* ego-browser
    invocations (e.g. a background deep-scan while another browser op ran),
    causing them to die with rc=-15 and lose offers. The ego server is a
    singleton managed by the ego app; spawning a fresh `ego-browser nodejs`
    wrapper each call is enough, and the caller's retry logic already
    re-spawns on empty snapshots. So cleanup now does nothing automatic.

    If a wedged server truly needs a reset, restart the ego app manually.
    """
    return


def _run_js(js: str, timeout: int = 150) -> str:
    """Run a JS script through ego-browser's node runtime; return stderr (its stdout).

    Safe for concurrent invocations: it does NOT kill other ego-browser
    processes (see _cleanup).
    """
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(js)
        proc = subprocess.run(
            f"{EGO_BIN} nodejs < {path}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        raise RuntimeError(f"ego-browser failed (rc={proc.returncode}): {proc.stderr[-600:]}")
    # ego-browser emits script output on stderr.
    return proc.stderr


def _snapshot_script(url: str, wait: int, profile: str) -> str:
    return f"""
(async () => {{
  const out = (o) => console.log(JSON.stringify(o));
  try {{
    const space = await ego.createTaskSpace('pracuj', {profile!r});
    await ego.useTaskSpace(space.id);
    const tab = await ego.createTab({url!r});
    const id = tab.targetId;
    await new Promise(r => setTimeout(r, {wait}));
    // Retry the snapshot a few times; keep the most complete capture.
    let best = '';
    for (let k = 0; k < 3; k++) {{
      const s = await ego.snapshot(id);
      const t = s ? (s.text || s.content || '') : '';
      if (t.length > best.length) best = t;
      if (best.length > 2000) break;
      await new Promise(r => setTimeout(r, 3000));
    }}
    out({{ok:true, text: best}});
  }} catch (e) {{
    out({{ok:false, err: String(e).slice(0, 600)}});
  }}
}})();
"""


def snapshot_url(url: str, wait: int = 14, profile: str = DEFAULT_PROFILE, timeout: int = 160) -> str:
    """Open a URL in ego-browser and return the accessibility snapshot text."""
    raw = _run_js(_snapshot_script(url, wait, profile), timeout)
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            data = json.loads(line)
            if data.get("ok"):
                return data["text"]
            raise RuntimeError(data.get("err"))
    raise RuntimeError(f"no JSON payload from ego-browser; raw tail:\n{raw[-500:]}")


def _parse_offers(txt: str, with_context: bool = False) -> list[dict]:
    lines = txt.split("\n")
    offers: list[dict] = []
    for i, line in enumerate(lines):
        m = _OFFER_RE.search(line)
        if not m:
            continue
        offer_url = m.group(1)
        # pracuj.pl's SPA returns "suggested" boilerplate offers (URL carries a
        # sug=... param) when a keyword has no real matches. These are NOT actual
        # hits for the query, so we drop them to avoid false positives. Any offer
        # with `sug=` in its URL is a fallback suggestion, never a real match.
        if "sug=" in offer_url:
            continue
        # Reliable, descriptive title comes from the URL slug
        # (e.g. .../praca/konsultant-ka-w-sklepie-...,oferta,ID -> "Konsultant Ka W Sklepie ...").
        try:
            slug = offer_url.split("/praca/")[1].split(",")[0]
            title = slug.replace("-", " ").strip().title()
        except Exception:
            title = ""
        if not title:
            lo, hi = max(0, i - 3), min(len(lines), i + 4)
            for j in range(lo, hi):
                if j == i:
                    continue
                tm = _TEXT_RE.search(lines[j])
                if tm and not title:
                    title = tm.group(1)
        # Capture a window of card text around the offer link so we can detect
        # remote/hybrid work mode from the search results (no detail fetch).
        ctx = ""
        if with_context:
            idx = txt.find(line)
            ctx = txt[max(0, idx - 500): idx + 400] if idx >= 0 else ""
        offers.append({
            "url": offer_url,
            "title": title,
            "slug": slug,
            "remote": bool(_REMOTE_RE.search(ctx)) if with_context else False,
            "context": ctx,
        })
    seen: set[str] = set()
    uniq: list[dict] = []
    for o in offers:
        if o["url"] not in seen:
            seen.add(o["url"])
            uniq.append(o)
    return uniq


def build_search_url(keyword: str, city: str | None = None, board: str = "it") -> str:
    """Build a pracuj.pl search URL.

    `it.pracuj.pl` is the IT-specific board (highest relevance for technical
    roles). The keyword goes in the path; `;kw/<city>` is the location filter.
    Remote filtering is done client-side from the result cards (pracuj.pl's SPA
    ignores work-mode URL params reliably), so there is no remote URL param.
    """
    base = "https://it.pracuj.pl" if board == "it" else "https://www.pracuj.pl"
    url = f"{base}/praca/{keyword}"
    if city:
        url += f";kw/{city}"
    return url


def search_offers(
    keyword: str,
    city: str | None = None,
    limit: int = 20,
    wait: int = 16,
    remote: bool = False,
    board: str = "it",
) -> list[dict]:
    """Return live pracuj.pl offers for a KEYWORD (IT board by default).

    pracuj.pl is an SPA: opening ``/praca/<keyword>`` (and ``;kw/<city>``) renders
    the real filtered results, which we parse from the accessibility snapshot.
    Remote offers are flagged from the result-card text (no detail fetch needed).
    Browsing only; no login required.

    ego-browser snapshots are occasionally empty (wedged server / slow SPA),
    so we retry the open+parse a few times before giving up.
    """
    url = build_search_url(keyword, city, board)
    offers: list[dict] = []
    for attempt in range(4):
        try:
            txt = snapshot_url(url, wait=wait, timeout=170)
        except Exception:  # noqa: BLE001 - retry a fresh browser process
            txt = ""
        offers = _parse_offers(txt, with_context=True)
        if len(offers) >= 3:
            break
        time.sleep(3)
    if remote:
        offers = [o for o in offers if o["remote"]]
    return offers[:limit]


def fetch_offer_text(url: str, wait: int = 12, retries: int = 2) -> str:
    """Return the cleaned job description text of a single offer detail page.

    Retries a few times: ego-browser occasionally dies (rc=-15 / empty
    snapshot) and a fresh process usually recovers it, so we don't lose an
    offer to a transient browser hiccup.
    """
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            txt = _clean_snapshot(snapshot_url(url, wait))
            if len(txt) >= 40:
                return txt
        except Exception as e:  # noqa: BLE001 - retry a fresh browser process
            last_err = e
    if last_err:
        raise RuntimeError(f"fetch_offer_text nieudany po {retries} próbach: {last_err}")
    return ""
