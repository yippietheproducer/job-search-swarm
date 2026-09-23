"""Regression tests for the deterministic crawler's filter rules.

These pin the three defects found by auditing the FIRST crawl run, which kept 447 rows
(of which 422 had no proven remote evidence, 247 had no part-time hint, and — the real
bug — the software-role filter did not exist at all). Each defect gets a test so it
cannot come back.

Also pins the specific non-dev rows that slipped through the loose filter, because they
are the exact shape of the false positive: they satisfy part-time + level and are titled
"Working Student ...", so only a real role filter rejects them.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load():
    p = ROOT / "swarm" / "crawl_sources.py"
    spec = importlib.util.spec_from_file_location("crawl_sources", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


crawl = _load()


# ── defect 1: there was no software-role filter ───────────────────────────────
@pytest.mark.parametrize("title", [
    "Working Student Brand Ambassador (m/f/d)",
    "Go-to-Market Working Student (m/f/d)",
    "Patient Outreach Specialist",
    "Working Student - Staffing Operations (f/m/d)",
    "Junior Crypto Analyst & Trader",
    "IT-Support Teilzeit / Werkstudenten (m/w/d)",
    "Working Student – AI Process & Business Excellence (m/f/d)",
    "Customer Retention & Account Manager",
    "Korepetytor online Python Pro",
])
def test_non_dev_titles_are_rejected(title):
    """These all passed the loose filter. They are the false-positive shape."""
    ok, why = crawl.is_software(title, "part-time remote flexible hours")
    assert not ok, f"{title!r} wrongly accepted ({why})"


@pytest.mark.parametrize("title", [
    "Python/Django Software Engineer (part-time, remote)",
    "Junior Full-Stack Developer (AI-Assisted)",
    "Frontend Engineer (React)",
    "Working Student – Software Development (f/m/d)",
    "Werkstudent:in Softwareentwicklung",
    "Shopify Full-Stack Developer",
    "Tester aplikacji webowych (QA / Playwright)",
    "Junior Data Scientist",
])
def test_real_dev_titles_are_accepted(title):
    ok, _ = crawl.is_software(title, "")
    assert ok, f"{title!r} wrongly rejected"


def test_dev_keyword_in_body_alone_is_not_enough_for_known_bad_titles():
    """The body fallback was the leak: a non-dev title with dev words in the body."""
    ok, _ = crawl.is_software("Patient Outreach Specialist",
                              "you will work with our engineering team on data pipelines")
    assert not ok


# ── defect 2: 247 rows passed with an empty part-time hint ────────────────────
@pytest.mark.parametrize("text", [
    "The position is part-time (~15-25 hours/week, with flexibility)",
    "Wymiar współpracy: szacunkowo 10-20 godzin tygodniowo",
    "Wymiar pracy: 0,5 etatu",
    "Teilzeit, 20 Stunden pro Woche",
    "Kapazität: Vollzeit oder Teilzeit, beides möglich",
    "Employment Type: Part-time",
    "16 h/week during term",
    "0.5 FTE",
])
def test_part_time_evidence_is_detected(text):
    assert crawl.PT_EVIDENCE.search(text), f"missed part-time in {text!r}"


@pytest.mark.parametrize("text", [
    "This is a full-time position, 40 hours per week",
    "Pełny etat, umowa o pracę",
    "Vollzeit, unbefristet",
])
def test_full_time_is_not_part_time_evidence(text):
    """PT_EVIDENCE must not match plain full-time text."""
    assert not crawl.PT_EVIDENCE.search(text), f"false part-time in {text!r}"


# ── defect 3: remote verdict 'UNKNOWN' was accepted as remote ─────────────────
def _row(**kw):
    base = {"title": "Python Developer", "text": "", "loc": "", "hours_facet": "",
            "schedule": "", "employmentType": "", "job_types": "", "level_score": 3,
            "remote_verdict": "UNKNOWN", "pt_hint": "", "hours": None, "http": 200}
    base.update(kw)
    return base


def test_unknown_remote_is_rejected_by_the_strict_filter():
    r = _row(text="part-time 20 hours/week", remote_verdict="UNKNOWN")
    assert crawl.qualifies(r, strict=False) is True, "loose filter is expected to allow it"
    assert crawl.qualifies(r, strict=True) is False, "strict must require remote evidence"


def test_remote_confirmed_passes():
    r = _row(text="part-time 20 hours/week. This is a fully remote role.",
             remote_verdict="REMOTE-CONFIRMED")
    assert crawl.qualifies(r) is True


def test_senior_is_rejected():
    r = _row(title="Senior Python Developer", text="part-time, fully remote, 20 hours/week",
             level_score=0)
    assert crawl.qualifies(r) is False


def test_careacross_shape_passes_the_strict_filter():
    """The one genuine win from the 8,234-posting employer-ATS sweep."""
    r = _row(title="Python/Django Software Engineer (part-time, remote, with flexible schedule)",
             text=("The position is part-time (~15-25 hours/week, with flexibility regarding "
                   "the specific working hours). Fully remote role. At least 2 years "
                   "experience with Python/Django."),
             remote_verdict="REMOTE-CONFIRMED")
    assert crawl.qualifies(r) is True


def test_the_four_false_positive_titles_do_not_qualify_as_a_set():
    """End-to-end: part-time + remote + junior, but not software => rejected."""
    for title in ("Working Student Brand Ambassador (m/f/d)",
                  "Go-to-Market Working Student (m/f/d)",
                  "Patient Outreach Specialist"):
        r = _row(title=title,
                 text="part-time 20 hours/week, fully remote",
                 remote_verdict="REMOTE-CONFIRMED")
        assert crawl.qualifies(r) is False, f"{title!r} leaked through"


# ── level classification ─────────────────────────────────────────────────────
def test_level_classification_orders_junior_above_senior():
    jun, *_ = crawl.classify("Working Student Software Development")
    mid, *_ = crawl.classify("Regular Python Developer")
    sen, *_ = crawl.classify("Senior Python Developer")
    assert jun > mid > sen
    assert sen == 0, "senior must be 0 so it fails the qualifies() gate"


def test_no_level_marker_is_neither_junior_nor_senior():
    score, label, _ = crawl.classify("Python Developer")
    assert score == 1 and label == "unknown"


# ── the crawler must not re-fetch to re-filter ───────────────────────────────
def test_crawler_dumps_raw_rows_for_offline_refiltering():
    """Filter iteration must not mean re-hammering ~450 endpoints."""
    src = (ROOT / "swarm" / "crawl_sources.py").read_text(encoding="utf-8")
    assert "crawl_raw.json" in src, "raw dump missing: filters could not be iterated offline"
    assert "strict=False" in src, "loose/strict comparison must stay available"


# ── defect 4: bare "flexible hours" is NOT part-time evidence ────────────────
@pytest.mark.parametrize("text", [
    "We offer flexible hours and a great culture.",
    "Full-time position, 40 hours per week, flexible hours",
    "You will work full time with flexible hours",
])
def test_flexible_hours_alone_is_not_part_time(text):
    """A full-time role routinely advertises flexible hours. Treating this as
    part-time evidence let four full-time poolside AI-infra roles through."""
    assert not crawl.PT_EVIDENCE.search(text), f"bare flexible-hours leaked in {text!r}"


def test_flexible_hours_alongside_a_real_part_time_marker_still_passes():
    assert crawl.PT_EVIDENCE.search("part-time, 20 hours/week, flexible hours")
