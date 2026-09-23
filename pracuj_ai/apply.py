"""Assisted apply: human-confirmed browser submission via ego-browser.

GOVERNED / HUMAN-IN-THE-LOOP. This module NEVER sends an application on its
own. Two commands:

  prepare  -> opens the offer in ego (reusing your logged-in session),
              clicks "Aplikuj na ofertę" (NEVER "Aplikuj szybko" = instant
              submit using the PREVIOUS application's data, no review),
              pre-fills the message with a tailored pitch (pracuj caps the
              field at 500 chars), best-effort fills screening questions,
              screenshots the form, and LEAVES IT OPEN for the human to
              review in the visible ego window.
  send     -> ONLY when the human types "wyślij". Reconnects to the open form
              tab and clicks "Wyślij formularz".

Why this shape (research-backed):
- pracuj.pl help: "Aplikuj szybko" submits with one click using your last
  application's data -> never automate it (no review, stale data, ban risk).
- The standard form (short message + CV + screening Q&A) is the safe path.
- 2026 AI-job-search best practice: auto-SUBMIT tools carry platform-ban /
  credibility risk; the highest-impact move is per-role CV tailoring + a
  human final "voice pass" before send. This module does exactly that:
  the agent prepares everything, the human does the final confirm.
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timezone

from . import browser

TASK_SPACE = "pracuj-apply"
STATE_FILE = pathlib.Path("applications.json")
MAX_MSG = 490  # pracuj message field maxlength=500


def _run_js(js: str, timeout: int = 220) -> dict:
    """Run JS through ego-browser; parse the last JSON `out({...})` payload."""
    raw = browser._run_js(js, timeout)
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    raise RuntimeError(f"brak JSON payload z ego-browser:\n{raw[-600:]}")


def make_pitch(materials: dict, max_chars: int = MAX_MSG) -> str:
    """<=max_chars application pitch from tailored materials.

    pracuj caps the employer-message field at 500 chars, so the full cover
    letter can't go there. We use a condensed pitch (first sentences) and
    leave the full letter for review / PDF attachment.
    """
    pitch = (materials.get("pitch") or "").strip()
    if not pitch:
        letter = materials.get("list_motywacyjny") or materials.get("letter") or ""
        sentences = re.split(r"(?<=[.!?])\s+", letter.strip())
        out = ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            cand = (out + " " + s).strip() if out else s
            if len(cand) <= max_chars:
                out = cand
            else:
                break
        pitch = out or letter[:max_chars].strip()
    if len(pitch) > max_chars:
        pitch = pitch[:max_chars].rsplit(" ", 1)[0].strip()
    return pitch


_PREPARE_TMPL = r"""
(async () => {
  const out = (o) => console.log(JSON.stringify(Object.assign({ok:false}, o)));
  try {
    const space = await ego.useOrCreateTaskSpace(__TS__);
    const tab = await ego.openOrReuseTab(__URL__, {wait: 6, timeout: 30000});
    await new Promise(r => setTimeout(r, 3500));
    let snap = await ego.snapshotText();
    let ref = null;
    for (const ln of snap.split('\n')) {
      if (ln.includes('Aplikuj na ofertę') && !ln.toLowerCase().includes('szybko')) {
        const m = ln.match(/ref=(\d+)/); if (m) { ref = m[1]; break; }
      }
    }
    if (!ref) { out({err: 'brak przycisku "Aplikuj na ofertę"', snap: snap.slice(0,700)}); return; }
    await ego.click('@'+ref);
    await new Promise(r => setTimeout(r, 4500));
    let formSnap = await ego.snapshotText();
    const info = await ego.pageInfo();
    let filled_msg = false;
    try { await ego.fillInput('css:textarea[name="message"]', __MSG__); filled_msg = true; }
    catch (e) { }
    let screening_filled = 0;
    try {
      const scr = __SCREENING__;
      for (const [q, a] of Object.entries(scr)) {
        const ql = q.toLowerCase();
        for (const ln of formSnap.split('\n')) {
          if (ln.toLowerCase().includes(ql.slice(0, 28))) {
            const m = ln.match(/ref=(\d+)/);
            if (m) { try { await ego.fillInput('@'+m[1], String(a)); screening_filled++; } catch(e){} break; }
          }
        }
      }
    } catch(e) {}
    let shot = null;
    try { const s = await ego.captureScreenshot(); shot = s && s.path; } catch(e){}
    out({ok:true, form_url: info.url, filled_msg: filled_msg, screening_filled: screening_filled, screenshot: shot, form_snap: formSnap.slice(0, 1000)});
  } catch (e) { out({err: String(e).slice(0, 600)}); }
})();
"""


def _state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(offer_url: str, materials: dict, db_path: str = "offers.db") -> dict:
    """Open the offer, click the (safe) apply button, fill the form, leave open.

    Returns the ego result dict. Records application state for later `send`.
    Does NOT submit.
    """
    from .store import offer_id

    pitch = make_pitch(materials)
    screening = materials.get("screening") or {}
    js = (_PREPARE_TMPL
          .replace("__TS__", json.dumps(TASK_SPACE))
          .replace("__URL__", json.dumps(offer_url))
          .replace("__MSG__", json.dumps(pitch))
          .replace("__SCREENING__", json.dumps(screening)))
    res = _run_js(js, timeout=220)
    if not res.get("ok"):
        raise RuntimeError(res.get("err") or "prepare nieudany")
    oid = offer_id(offer_url)
    st = _state()
    st[oid] = {
        "offer_url": offer_url,
        "form_url": res.get("form_url"),
        "pitch": pitch,
        "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "prepared",
        "screenshot": res.get("screenshot"),
    }
    _save_state(st)
    return res


_SEND_TMPL = r"""
(async () => {
  const out = (o) => console.log(JSON.stringify(Object.assign({ok:false}, o)));
  try {
    const space = await ego.useOrCreateTaskSpace(__TS__);
    const tabs = await ego.listTabs();
    let target = null;
    for (const t of (tabs || [])) {
      const u = ((t.url||'') + ' ' + (t.title||'') + ' ' + (t.name||'')).toLowerCase();
      if (u.includes('aplikowanie') || u.includes('formularz') || (u.includes('aplikuj') && u.includes('ofert'))) { target = t; break; }
    }
    if (!target) { out({err: 'brak otwartego formularza — uruchom prepare'}); return; }
    const u = target.url || target.targetUrl || target.pageUrl || target.link;
    if (!u) { out({err: 'brak url formularza w tabie'}); return; }
    const tab = await ego.openOrReuseTab(u, {wait: 3, timeout: 20000});
    await new Promise(r => setTimeout(r, 2500));
    let snap = await ego.snapshotText();
    let ref = null;
    for (const ln of snap.split('\n')) {
      const low = ln.toLowerCase();
      if ((low.includes('wyślij formularz') || low.includes('wyslij formularz') || (low.includes('wyślij') && low.includes('form'))) && !low.includes('anuluj')) {
        const m = ln.match(/ref=(\d+)/); if (m) { ref = m[1]; break; }
      }
    }
    if (!ref) { out({err: 'nie znaleziono przycisku Wyślij formularz', snap: snap.slice(0,700)}); return; }
    await ego.click('@'+ref);
    await new Promise(r => setTimeout(r, 4500));
    const info = await ego.pageInfo();
    let shot = null;
    try { const s = await ego.captureScreenshot(); shot = s && s.path; } catch(e){}
    out({ok:true, url_after: info.url, screenshot: shot});
  } catch (e) { out({err: String(e).slice(0, 600)}); }
})();
"""


def send(offer_id: str | None = None, db_path: str = "offers.db") -> dict:
    """HUMAN-IN-THE-LOOP submit. ONLY call after explicit human 'wyślij'.

    Finds the open application form tab in the pracuj-apply task space and
    clicks 'Wyślij formularz'. Records status='sent'.
    """
    js = _SEND_TMPL.replace("__TS__", json.dumps(TASK_SPACE))
    res = _run_js(js, timeout=120)
    if not res.get("ok"):
        raise RuntimeError(res.get("err") or "send nieudany")
    st = _state()
    if offer_id and offer_id in st:
        st[offer_id]["status"] = "sent"
        st[offer_id]["sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        st[offer_id]["screenshot_sent"] = res.get("screenshot")
        _save_state(st)
    return res


def status() -> dict:
    return _state()
