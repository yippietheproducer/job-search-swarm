"""Discovery: monitor pracuj.pl for jobs matching the candidate profile.

STATUS: reference / Phase 2. The scraping surface is intentionally conservative
and human-supervised.

Why human-in-the-loop matters here:
- pracuj.pl sits behind Cloudflare bot protection. Aggressive automated
  crawling risks IP/account bans and violates their Terms of Service.
- The high-value, low-risk path is: the candidate browses or exports a shortlist
  of job URLs / descriptions, drops them into a folder, and `pracuj-ai tailor`
  processes each one. This keeps the human as the actor (ToS-compliant) while AI
  does the heavy tailoring.
- A future OPT-IN browser monitor (built on `browser-autonomy` + ego-browser,
  reusing the candidate's real logged-in session) can surface new matches, but it
  must throttle aggressively and never auto-apply.

This module provides a safe local loader: read job descriptions the candidate
has collected, so the rest of the pipeline stays fully offline until the human
chooses to apply.
"""
from __future__ import annotations

import pathlib


def load_job_folder(folder: str | pathlib.Path) -> list[tuple[str, str]]:
    """Return [(job_name, job_text)] for every .txt file in a folder."""
    folder = pathlib.Path(folder)
    jobs = []
    for p in sorted(folder.glob("*.txt")):
        jobs.append((p.stem, p.read_text(encoding="utf-8")))
    return jobs
