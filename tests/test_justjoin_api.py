"""Tests for the justjoin/rocketjobs multi-category source.

No network: every test injects a `get`. The fixtures are trimmed copies of real payloads, keeping
the exact field names and — critically — the real `unit` values, because the unit handling is the
bug this module exists to prevent.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pracuj_ai import justjoin_api as J  # noqa: E402


# ── the category bug this module exists to prevent ───────────────────────────
def test_the_query_set_includes_the_categories_that_hold_the_work():
    """part_time alone found 1 qualifying role; the contract facets found 9+.

    A search phrased in the user's words ("part-time") must not query only the facet matching
    those words — the work is labelled b2b_contract.
    """
    assert J.PART_TIME in J.CONTRACT_FACETS
    assert J.B2B_CONTRACT in J.CONTRACT_FACETS
    assert J.FREELANCE in J.CONTRACT_FACETS


def test_url_requests_one_facet_plus_remote():
    u = J.listing_url(J.B2B_CONTRACT, from_=20, remote=True)
    assert "workingTimes=b2b_contract" in u and "remoteWorkOptions=remote" in u and "from=20" in u


def test_url_can_add_levels():
    assert "experienceLevels=junior|mid" in J.listing_url(levels=("junior", "mid"))


def test_url_rejects_negative_offset():
    with pytest.raises(ValueError):
        J.listing_url(from_=-1)


def test_sweep_pages_all_three_facets_by_default():
    calls = []

    def get(url):
        calls.append(url)
        return {"data": [], "meta": {"totalItems": 0}}

    J.sweep(get=get, sleep=0)
    seen = {c.split("workingTimes=")[1].split("&")[0] for c in calls}
    assert seen == set(J.CONTRACT_FACETS)


# ── TRAP 1: units. unit='Hour' vs unit='Month', a 168x error ─────────────────
def test_hourly_unit_is_hourly():
    et = [{"type": "b2b", "currency": "PLN", "unit": "Hour",
           "fromPerUnit": 180.0, "toPerUnit": 200.0}]
    lo, hi, how = J.monthly_at(et, 20)
    assert round(lo) == round(180 * 20 * 52 / 12) == 15600
    assert "zl/h" in how


def test_monthly_unit_is_monthly_and_must_be_prorated():
    """EduGO really returned fromPerUnit=25000 with unit='Month'.

    Reading that as an hourly rate gives 25,000 zl/h — a 168x overstatement — and it looked
    like the best-paying role on the board.
    """
    et = [{"type": "b2b", "currency": "PLN", "unit": "Month",
           "fromPerUnit": 25000.0, "toPerUnit": 30000.0}]
    lo, hi, how = J.monthly_at(et, 20)
    assert lo == 12500.0, "a 25,000/month figure at 20h/week is half of it, not 25,000"
    assert hi == 15000.0
    assert "FT" in how


def test_monthly_unit_at_40h_is_not_halved():
    et = [{"type": "b2b", "currency": "PLN", "unit": "Month",
           "fromPerUnit": 20000.0, "toPerUnit": 20000.0}]
    lo, _, _ = J.monthly_at(et, 40)
    assert lo == 20000.0


def test_unknown_unit_is_refused_rather_than_guessed():
    et = [{"type": "b2b", "currency": "PLN", "unit": "Week", "fromPerUnit": 5000.0}]
    lo, hi, how = J.monthly_at(et, 20)
    assert lo is None and "unhandled unit" in how


def test_only_native_pln_is_used_not_converted_duplicates():
    """Converted currencies are derived from the PLN row; using them adds no information."""
    et = [{"type": "b2b", "currency": "USD", "unit": "Hour", "fromPerUnit": 40.0},
          {"type": "b2b", "currency": "PLN", "unit": "Hour", "fromPerUnit": 160.0}]
    lo, _, how = J.monthly_at(et, 20)
    assert round(lo) == round(160 * 20 * 52 / 12)
    assert "160" in how


def test_no_b2b_rate_is_reported_as_such_not_as_zero():
    lo, hi, how = J.monthly_at([{"type": "permanent", "currency": "PLN", "unit": "Month",
                                 "fromPerUnit": 10000.0}], 20)
    assert lo is None and "no native PLN b2b rate" in how


def test_zero_hours_is_rejected():
    with pytest.raises(ValueError):
        J.monthly_at([], 0)


# ── TRAP 2: duplication, measured at 17x ────────────────────────────────────
def _offer(company="Acme", title="Python Developer", **kw):
    base = {
        "title": title, "companyName": company, "slug": f"{company}-{title}".lower().replace(" ", "-"),
        "experienceLevel": "mid", "workingTime": "b2b_contract", "workplaceType": "remote",
        "languages": [{"code": "en", "level": "B2"}],
        "requiredSkills": [{"name": "Python"}],
        "employmentTypes": [{"type": "b2b", "currency": "PLN", "unit": "Hour",
                             "fromPerUnit": 120.0, "toPerUnit": 150.0}],
        "expiredAt": "2026-10-10",
    }
    base.update(kw)
    return base


def test_dedupe_collapses_city_rows_for_one_role():
    rows = [J.normalize(_offer(slug=f"s{i}")) for i in range(17)]
    assert len(J.dedupe(rows)) == 1, "17 city rows are ONE role"


def test_dedupe_keeps_genuinely_different_roles():
    rows = [J.normalize(_offer(title="Python Developer")),
            J.normalize(_offer(title="React Developer")),
            J.normalize(_offer(company="Other"))]
    assert len(J.dedupe(rows)) == 3


def test_dedupe_drops_rows_with_no_title():
    assert J.dedupe([J.normalize(_offer(title=""))]) == []


def test_sweep_reports_rows_and_roles_separately():
    """3 city rows of one role must report rows=3, roles=1, duplicate_factor=3."""
    dupes = [_offer(slug=f"x{i}") for i in range(3)]
    pages = {0: {"data": dupes, "meta": {"totalItems": 3}},
             10: {"data": [], "meta": {"totalItems": 3}}}
    out = J.sweep(facets=("b2b_contract",), get=lambda u: pages[0 if "from=0" in u else 10],
                  sleep=0)
    f = out["per_facet"]["b2b_contract"]
    assert (f["rows"], f["roles"], f["duplicate_factor"]) == (3, 1, 3.0)


# ── TRAP 3: 10000 is a cap, not a count ─────────────────────────────────────
def test_a_total_of_10000_is_flagged_as_capped():
    assert J.is_capped(10000) is True
    assert J.is_capped(9999) is False
    assert J.is_capped(None) is False
    assert J.is_capped("10000") is True


def test_sweep_flags_a_capped_total():
    out = J.sweep(facets=("full_time",),
                  get=lambda u: {"data": [], "meta": {"totalItems": 10000}}, sleep=0)
    assert out["per_facet"]["full_time"]["total_is_capped"] is True


# ── normalize / languages / skills ──────────────────────────────────────────
def test_normalize_extracts_the_useful_fields():
    r = J.normalize(_offer())
    assert r["level"] == "mid" and r["is_remote"] is True and r["english_only"] is True
    assert r["german_required"] is False and r["skills"] == ["Python"]
    assert r["url"].startswith("https://justjoin.it/job-offer/")


def test_german_required_is_detected():
    r = J.normalize(_offer(languages=[{"code": "de"}, {"code": "en"}]))
    assert r["german_required"] is True and r["english_only"] is False


def test_normalize_never_raises_on_junk():
    assert J.normalize(None) == {}
    assert J.normalize({"title": "X"})["monthly_low"] is None


# ── qualify: the four gates ─────────────────────────────────────────────────
def test_qualify_accepts_the_experis_shape():
    """Experis: mid, remote, freelance, en-only, 150-155 zl/h = 13,000-13,433 at 20h."""
    r = J.normalize(_offer(title="AI Platform Engineer", employmentTypes=[
        {"type": "b2b", "currency": "PLN", "unit": "Hour",
         "fromPerUnit": 150.0, "toPerUnit": 155.0}]))
    ok, why = J.qualify(r)
    assert ok, why


@pytest.mark.parametrize("kw,reason", [
    ({"experienceLevel": "senior"}, "senior-only"),
    ({"workplaceType": "hybrid"}, "not remote"),
    ({"languages": [{"code": "de"}]}, "German required"),
    ({"employmentTypes": [{"type": "b2b", "currency": "PLN", "unit": "Hour",
                           "fromPerUnit": 20.0, "toPerUnit": 30.0}]}, "below"),
    ({"employmentTypes": []}, "no native PLN"),
])
def test_qualify_rejects_each_gate(kw, reason):
    ok, why = J.qualify(J.normalize(_offer(**kw)))
    assert not ok and reason in why


def test_qualify_keeps_mid_and_rejects_only_senior_by_level():
    """A shared level helper must not treat 'mid' as gated — DATAIQ was lost once that way."""
    assert J.level_of({"experienceLevel": "mid"}) == "mid"
    assert J.qualify(J.normalize(_offer(experienceLevel="mid")))[0] is True


def test_shortlist_sorts_by_pay_descending_and_filters():
    """Titles must be real dev titles now that the role family is gated."""
    good = J.normalize(_offer(title="Python Developer", employmentTypes=[
        {"type": "b2b", "currency": "PLN", "unit": "Hour", "fromPerUnit": 150.0, "toPerUnit": 160.0}]))
    weak = J.normalize(_offer(title="Cloud Engineer", employmentTypes=[
        {"type": "b2b", "currency": "PLN", "unit": "Hour", "fromPerUnit": 90.0, "toPerUnit": 110.0}]))
    dead = J.normalize(_offer(title="Python Developer", experienceLevel="senior"))
    out = J.shortlist([weak, good, dead])
    assert [r["title"] for r in out] == ["Python Developer", "Cloud Engineer"], \
        "senior must be filtered, better-paid first"


# ── resilience: one dead page must not kill the sweep ───────────────────────
def test_sweep_survives_a_facet_that_errors():
    def get(url):
        if "workingTimes=freelance" in url:
            raise RuntimeError("HTTP 503")
        return {"data": [_offer()], "meta": {"totalItems": 1}}

    out = J.sweep(get=get, sleep=0)
    assert "error" in out["per_facet"]["freelance"]
    assert out["per_facet"]["b2b_contract"]["roles"] == 1


def test_a_failed_facet_stays_visible_and_is_not_reported_as_empty():
    """The silent-drop failure mode: an errored facet must never be summarised as 0 rows.

    My first implementation set the error, then unconditionally overwrote it with the summary
    dict - so a facet that died looked exactly like a facet that found nothing.
    """
    def get(url):
        if "workingTimes=freelance" in url:
            raise RuntimeError("HTTP 503")
        return {"data": [_offer()], "meta": {"totalItems": 1}}

    out = J.sweep(get=get, sleep=0)
    f = out["per_facet"]["freelance"]
    assert f["ok"] is False and "503" in f["error"]
    assert out["failed_facets"] == ["freelance"]
    assert out["meta"]["failed_facets"] == 1
    assert out["per_facet"]["b2b_contract"]["ok"] is True


def test_a_healthy_sweep_reports_no_failures():
    out = J.sweep(facets=("part_time",),
                  get=lambda u: {"data": [_offer()], "meta": {"totalItems": 1}}, sleep=0)
    assert out["failed_facets"] == [] and out["per_facet"]["part_time"]["ok"] is True


def test_rate_formatter_handles_a_missing_upper_bound():
    et = [{"type": "b2b", "currency": "PLN", "unit": "Hour", "fromPerUnit": 160.0}]
    lo, hi, how = J.monthly_at(et, 20)
    assert hi is None and "160" in how and "-" not in how


# ── the title is authoritative, and the role family must be checked ─────────
def test_senior_in_the_title_rejects_even_when_the_field_says_mid():
    """Netguru's '(Senior) Frontend Developer with React' carries experienceLevel='mid'.

    A field-only check kept a senior ad - and with it the 503.874 zl/h anomaly at the top of the
    shortlist.
    """
    r = J.normalize(_offer(title="(Senior) Frontend Developer with React and Node"))
    ok, why = J.qualify(r)
    assert not ok and "title names a senior" in why


@pytest.mark.parametrize("title,level", [
    ("Fractional CTO - freelance", "c_level"),
    ("SAP RICE Programme Manager", "manager"),
    ("Tech Lead (FinTech/Reg&Compliance)", "manager"),
    ("Front End Solution Architect", "mid"),
])
def test_non_engineering_titles_and_levels_are_rejected(title, level):
    """All four passed the rate-only filter live and inflated the shortlist to 155 rows."""
    ok, why = J.qualify(J.normalize(_offer(title=title, experienceLevel=level)))
    assert not ok, f"{title!r} leaked ({why})"


@pytest.mark.parametrize("title", [
    "SAP SuccessFactors ESM Consultant", "Business Intelligence Expert with QlikView",
    "Medallia Platform Expert",
])
def test_non_engineering_titles_from_the_live_155_are_rejected(title):
    """All three appeared in the live 155 because the role family was unchecked."""
    ok, why = J.qualify(J.normalize(_offer(title=title)))
    assert not ok, f"{title!r} leaked ({why})"


def test_an_off_stack_but_ENGINEERING_role_passes_the_gates_and_ranks_low():
    """'Guidewire Developer' is real engineering with a stack gap.

    The correct treatment is to let it through the gates and score it low on stack_fit - NOT to
    reject it as non-engineering. Conflating 'not my stack' with 'not engineering' would throw
    away real roles; the two concerns are deliberately separate functions.
    """
    r = J.normalize(_offer(title="Guidewire Developer (BillingCenter)",
                           requiredSkills=[{"name": "Guidewire"}, {"name": "Gosu"}]))
    assert J.qualify(r)[0] is True, "it IS a developer role"
    assert J.stack_fit(r) == 0, "...but none of it is his stack"


@pytest.mark.parametrize("title", [
    "AI Engineer (LLM & Agentic Systems)", "Cloud Engineer (AWS + Python)",
    "Full Stack Engineer (React & Java)", "BI Data Engineer", "Python Developer",
    "Cloud Software Developer",
])
def test_real_dev_titles_still_pass(title):
    ok, why = J.qualify(J.normalize(_offer(title=title)))
    assert ok, f"{title!r} wrongly rejected ({why})"


def test_role_family_classification():
    assert J.role_family({"title": "Python Developer"})[0] == "dev"
    assert J.role_family({"title": "Korepetytor online Python"})[0] == "nondev"
    assert J.role_family({"title": "Ninja"})[0] == "unknown"


def test_stack_fit_ranks_without_gating():
    """A Python+AWS ad must outrank an off-stack ad at equal pay."""
    py = J.normalize(_offer(title="Cloud Engineer", requiredSkills=[{"name": "Python"}, {"name": "AWS"}]))
    off = J.normalize(_offer(title="Developer", requiredSkills=[{"name": "COBOL"}]))
    assert J.stack_fit(py) > J.stack_fit(off)
    assert J.stack_fit(off) == 0


def test_shortlist_breaks_ties_on_pay_then_stack_fit():
    a = J.normalize(_offer(title="Cloud Engineer", requiredSkills=[{"name": "Python"}, {"name": "AWS"},
                       {"name": "Docker"}]))
    b = J.normalize(_offer(title="Developer"))  # same rate, fewer matching skills
    out = J.shortlist([b, a])
    assert out[0]["title"] == "Cloud Engineer"
