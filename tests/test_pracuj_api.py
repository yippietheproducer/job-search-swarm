"""Tests for the pracuj.pl listing-API instrument.

No network: every test injects a `get`. The payloads below are trimmed copies of the real
response, keeping the field names and the two Polish chip vocabularies that the code parses.
"""
from __future__ import annotations

import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pracuj_ai import pracuj_api as P  # noqa: E402


# ── URL building: the facets must actually reach the query string ─────────────
def test_url_carries_the_proven_part_time_facet():
    u = P.listing_url(part_time=True, remote=True, it_only=True)
    assert "ws=1" in u and "wm=home-office" in u and "subservice=1" in u


def test_url_can_express_not_part_time():
    """Required to test the filter: ws=0 must be expressible, or the facet is unprovable."""
    assert "ws=0" in P.listing_url(part_time=False)


def test_omitting_a_facet_omits_it_entirely():
    u = P.listing_url(part_time=None, remote=False, it_only=None)
    assert "ws=" not in u and "wm=" not in u and "subservice=" not in u


def test_url_rejects_bad_inputs():
    with pytest.raises(ValueError):
        P.listing_url(kind="nonsense")
    with pytest.raises(ValueError):
        P.listing_url(page=0)
    with pytest.raises(ValueError):
        P.listing_url(per_page=0)


def test_count_reads_offers_count():
    assert P.count(get=lambda u: {"offersCount": 46},
                   part_time=True, remote=True, it_only=True) == 46


def test_count_tolerates_a_missing_field():
    assert P.count(get=lambda u: {}) == 0


# ── the Remotive bug class: two values must give two answers ─────────────────
def test_part_time_facet_is_provable_with_two_payloads():
    """The regression test for silently-ignored filters."""
    def get(u: str):
        return {"offersCount": 46 if "ws=1" in u else 2233}

    yes = P.count(get=get, part_time=True, remote=True, it_only=True)
    no = P.count(get=get, part_time=False, remote=True, it_only=True)
    assert (yes, no) == (46, 2233)
    assert yes != no, "facet did not change the result set -> it is being ignored"


# ── level classification, from the board's own chips ─────────────────────────
@pytest.mark.parametrize("chips,expected", [
    (["Młodszy specjalista / Młodsza specjalistka (junior)"], "junior"),
    (["Specjalista / Specjalistka (mid / Regular)"], "mid"),
    (["Starszy specjalista / Starsza specjalistka (senior)"], "senior"),
    (["Ekspert / Ekspertka"], "senior"),
    (["Architekt/ekspert"], "senior"),
    (["Kierownik / Kierowniczka", "Menedżer / Menedżerka"], "senior"),
    ([], "unknown"),
])
def test_level_of(chips, expected):
    assert P.level_of(chips)[0] == expected


def test_a_role_open_to_mid_and_senior_is_MID_eligible_but_flagged():
    """The most junior tier present wins, and the senior tier stays visible.

    My first version let senior win outright, which rejected DATAIQ - the project's own #1
    pick - because its ad lists both mid and senior. The rule was stricter than the market.
    """
    lvl, also_senior = P.level_of(["Specjalista (mid / Regular)",
                                   "Starszy specjalista (senior)"])
    assert lvl == "mid", "an ad open to mid must be mid-eligible"
    assert also_senior is True, "the senior tier must stay visible, not be discarded"


def test_starszy_specjalista_is_senior_despite_containing_the_word_specjalista():
    """'Starszy specjalista' contains 'specjalista'; a naive substring test calls it mid."""
    assert P.level_of(["Starszy specjalista / Starsza specjalistka (senior)"])[0] == "senior"
    assert P.level_of(["Ekspert / Ekspertka"])[0] == "senior"


def test_senior_only_is_senior_and_not_flagged_as_more_junior():
    lvl, also = P.level_of(["Starszy specjalista (senior)"])
    assert (lvl, also) == ("senior", True)


# ── normalize: the facets become columns, city rows fan out ──────────────────
GROUP = {
    "groupId": "b4280000-56be-0050-c083-08df040a9e2a",
    "jobTitle": "AI Developer",
    "companyName": "DATAIQ",
    "salaryDisplayText": "140–180 zł netto/h",
    "jobDescription": "The role starts in a part-time capacity (~20 hours per week).",
    "positionLevels": ["Specjalista (mid / Regular)", "Starszy specjalista (senior)"],
    "typesOfContract": ["Kontrakt B2B"],
    "workSchedules": ["Część etatu"],
    "workModes": ["Praca zdalna"],
    "technologies": ["Python", "FastAPI"],
    "initialPublicated": "2026-08-28T08:12:24.99Z",
    "expirationDate": "2026-09-27T21:59:59Z",
    "offers": [
        {"partitionId": 1005106432, "displayWorkplace": "Warszawa", "isWholePoland": True,
         "offerAbsoluteUri": "https://www.pracuj.pl/praca/ai-developer-warszawa,oferta,1005106432"},
        {"partitionId": 1005106433, "displayWorkplace": "Kraków", "isWholePoland": True,
         "offerAbsoluteUri": "https://www.pracuj.pl/praca/ai-developer-krakow,oferta,1005106433"},
    ],
}


def test_normalize_fans_out_one_row_per_city():
    rows = P.normalize(GROUP)
    assert len(rows) == 2
    assert {r["city"] for r in rows} == {"Warszawa", "Kraków"}


def test_normalize_surfaces_work_schedules_as_a_column():
    """This is the blind spot: offers.db had NO schedule column at all."""
    r = P.normalize(GROUP)[0]
    assert r["work_schedules"] == ["Część etatu"]
    assert r["is_part_time"] is True
    assert r["is_remote"] is True
    assert r["work_modes"] == ["Praca zdalna"]


def test_normalize_carries_the_body_so_hours_need_no_browser():
    r = P.normalize(GROUP)[0]
    assert "20 hours per week" in r["body"]
    assert r["expires"] == "2026-09-27T21:59:59Z"


def test_normalize_rejects_malformed_groups_without_raising():
    assert P.normalize({}) == []
    assert P.normalize({"jobTitle": "X"}) == []          # no offers
    assert P.normalize({"offers": [{"partitionId": 1}]}) == []   # no title
    assert P.normalize("not a dict") == []


def test_normalize_skips_offers_without_a_url():
    g = {**GROUP, "offers": [{"partitionId": 1}, *GROUP["offers"]]}
    assert len(P.normalize(g)) == 2


# ── sweep: roles vs rows must never be confused ───────────────��──────────────
def test_sweep_pages_and_dedupes_and_reports_both_counts():
    pages = {
        1: {"groupedOffers": [GROUP], "offersTotalCount": 46,
            "groupedOffersTotalCount": 28},
        2: {"groupedOffers": [GROUP], "offersTotalCount": 46,
            "groupedOffersTotalCount": 28},   # duplicate page => stops
    }
    out = P.sweep(get=lambda u: pages[1 if "pn=1" in u else 2],
                  part_time=True, remote=True, it_only=True)
    assert out["meta"]["rows"] == 2, "the duplicate page must not double-count rows"
    assert out["meta"]["roles"] == 1, "two city rows are ONE role"
    assert out["meta"]["offers_total_count"] == 46
    assert out["meta"]["grouped_offers_total_count"] == 28


def test_sweep_reports_rows_and_roles_as_separate_numbers():
    """The phantom '15x gap' came from comparing a row count against a role count."""
    out = P.sweep(get=lambda u: {"groupedOffers": [GROUP], "offersTotalCount": 46,
                                 "groupedOffersTotalCount": 28},
                  part_time=True, remote=True, it_only=True)
    assert out["meta"]["rows"] != out["meta"]["roles"]


def test_sweep_handles_an_empty_board():
    out = P.sweep(get=lambda u: {"groupedOffers": [], "offersTotalCount": 0})
    assert out["rows"] == [] and out["meta"]["rows"] == 0


# ── qualify: the four hard filters ───────────────────────────────────────────
def _row(**kw):
    base = {"title": "AI Developer", "is_part_time": True, "is_remote": True,
            "level": "mid", "body": "part-time, 20 hours per week, fully remote"}
    base.update(kw)
    return base


def test_qualify_accepts_the_real_shortlist_shape():
    ok, why = P.qualify(_row())
    assert ok, why


@pytest.mark.parametrize("kw,reason", [
    ({"is_part_time": False}, "not part-time"),
    ({"is_remote": False}, "not remote"),
    ({"level": "senior"}, "senior"),
    ({"title": "Technical Product Owner"}, "non-software title"),
    ({"title": "Team Lead"}, "non-software title"),
])
def test_qualify_rejects_each_way(kw, reason):
    ok, why = P.qualify(_row(**kw))
    assert not ok and reason in why


def test_qualify_rejects_the_rows_the_board_actually_returned():
    """Every one of these really appeared in the 46-row facet."""
    for title, level in [
        ("Technical Product Owner", "mid"),
        ("SAP Basis Consultant – Administrator SAP Basis", "senior"),
        ("Senior Veeva eTMF/CTMS Consultant", "senior"),
        ("IT Project Manager (Business / Transformation)", "mid"),
        ("Internship – Software Asset Management", "junior"),
        ("Poland Tutor | Python", "mid"),
    ]:
        ok, why = P.qualify(_row(title=title, level=level))
        assert not ok, f"{title!r} leaked ({why})"


# ── the body-override rule (found by running against live data) ──────────────
def test_body_verdict_reads_part_time_hours_from_the_body():
    v, why = P.body_verdict("The role starts in a part-time capacity (~20 hours per week), "
                            "with the potential to scale up to full-time commitment later.")
    assert v == "part-time", why


def test_body_full_time_overrides_a_permissive_part_time_chip():
    """Monterail's chip lists BOTH schedules while its body says Full-time.

    Verified live: `workSchedules: ["Pełny etat","Część etatu"]` and the body said
    "Availability: Full-time (B2B contract/hourly rates)." The chip matches because
    "Część etatu" is in the list; without this rule the row is a false positive.
    """
    r = _row(title="Regular Fullstack Developer (Vue.js + .Net) - Freelancer",
             body="Availability: Full-time (B2B contract/hourly rates).")
    ok, why = P.qualify(r)
    assert not ok and "asserts full-time" in why


def test_explicit_part_time_hours_survive_a_passing_full_time_mention():
    """DATAIQ: 'starts in a part-time capacity (~20 hours per week), with the potential to
    scale up to full-time'. A naive full-time substring test kills the best role we have."""
    v, _ = P.body_verdict("starts in a part-time capacity (~20 hours per week), with the "
                          "potential to scale up to full-time commitment in the future")
    assert v == "part-time"


def test_body_with_no_hours_is_not_treated_as_full_time():
    v, why = P.body_verdict("We are a fast-growing company.")
    assert v == "unknown" and "no hours" in why


def test_dat_aiq_shape_now_survives_the_full_pipeline():
    """The end-to-end regression: our #1 pick must not be filtered out by our own code."""
    row = {
        "title": "AI Developer", "is_part_time": True, "is_remote": True,
        "level": "mid", "also_senior": True,
        "body": "The role starts in a part-time capacity (~20 hours per week), with the "
                "potential to scale up to full-time commitment in the future.",
    }
    ok, why = P.qualify(row)
    assert ok, why


# ── the honest flag: the listing text field is a SUMMARY, not the body ───────
def test_annotate_flags_rows_whose_text_field_is_silent_about_hours():
    """The listing API cannot prove part-time when its text field is silent.

    Measured: APRIORIT's Intern C++ posting has a 90-character `jobDescription`. So absence
    of an hours statement in this field is NOT evidence of part-time, and flagging it is the
    only honest treatment.
    """
    row = P.annotate(_row(body="Your responsibilities, Visiting lectures., Doing homework."))
    assert row["hours_verdict"] == "unknown"
    assert row["needs_body_check"] is True


def test_annotate_clears_the_flag_when_the_text_field_has_hours():
    row = P.annotate(_row(body="Stabilne zatrudnienie na Umowę o Pracę (1/2 etatu)"))
    assert row["hours_verdict"] == "part-time"
    assert row["needs_body_check"] is False


def test_sweep_output_rows_are_annotated():
    out = P.sweep(get=lambda u: {"groupedOffers": [GROUP], "offersTotalCount": 2,
                                 "groupedOffersTotalCount": 1},
                  part_time=True, remote=True, it_only=True)
    r = out["rows"][0]
    assert "needs_body_check" in r and "hours_verdict" in r


def test_the_real_daiquote_from_lane15_is_read_as_part_time():
    """Lane 15 quoted this verbatim from the offer page."""
    v, why = P.body_verdict("The role starts in a part-time capacity (~20 hours per week), "
                            "with the potential to scale up to full-time commitment in the "
                            "future as the project grows.")
    assert v == "part-time" and "20" in why


# ── the 'develop' substring trap ─────────────────────────────────────────────
@pytest.mark.parametrize("title", [
    "Business Development Representative",
    "Business Development Manager",
    "Sales Development Representative",
    "Przedstawiciel Handlowy",
    "Doradca Klienta",
    "Asystent ds. sprzedaży",
    "Lektor języka angielskiego",
])
def test_non_dev_titles_that_contain_a_dev_keyword_are_rejected(title):
    """'Business Development' contains 'develop', so it satisfied the dev test.

    Found by sweeping all 539 part-time remote Polish offers instead of only the 46 IT ones -
    the broader sweep is what surfaced a weather company's BD rep passing an engineering
    filter.
    """
    ok, why = P.qualify(_row(title=title))
    assert not ok, f"{title!r} leaked ({why})"


def test_a_real_developer_title_still_passes_after_the_tightening():
    for t in ("AI Developer", "Python Developer", "Frontend Developer",
              "Fullstack Developer (Vue.js + .Net)", "Software Engineer",
              "Technical QA Engineer / Tester Oprogramowania"):
        ok, why = P.qualify(_row(title=t))
        assert ok, f"{t!r} wrongly rejected ({why})"


# ── the contract facet (tc), discovered late and 48x larger than part-time ──
def test_url_can_carry_the_contract_facet():
    """ws=1 shows 46 remote IT offers; tc=3 shows 2,209. The facet was missing entirely."""
    u = P.listing_url(remote=True, it_only=True, contract=P.CONTRACT_B2B)
    assert "tc=3" in u


def test_contract_facet_is_numeric_not_named():
    """`tc=b2b` raises HTTPError; only the numeric form works, so the constant must be numeric."""
    assert P.CONTRACT_B2B == "tc=3" and P.CONTRACT_B2B.split("=")[1].isdigit()


def test_omitting_contract_leaves_it_out():
    assert "tc=" not in P.listing_url(remote=True, it_only=True)


def test_normalize_surfaces_types_of_contract():
    r = P.normalize({**GROUP, "typesOfContract": ["Kontrakt B2B", "Umowa o dzieło"]})
    assert "Kontrakt B2B" in r[0]["contracts"]


# ── the NBSP units trap inside pracuj's own salary strings ──────────────────
def test_hourly_rate_parses_pracujs_nbsp_format():
    """'200–250\\u00a0zł netto (+\\u00a0VAT)\\u00a0/ godz.' -> (200, 250).

    A naive .replace(' ', '') leaves '20\\xa0000' and raises ValueError on int(). Fourth
    units/formatting trap in this project, second involving NBSP.
    """
    lo, hi, how = P.parse_hourly_rate("200\u2013250\xa0zł netto (+\xa0VAT)\xa0/ godz.")
    assert (lo, hi) == (200, 250) and "zł/h" in how


def test_hourly_rate_handles_a_nbsp_thousands_separator():
    lo, hi, _ = P.parse_hourly_rate("20\xa0000–30\xa0000 zł netto / godz.")
    assert lo == 20000 and hi == 30000


def test_single_hourly_figure_yields_no_upper_bound():
    lo, hi, _ = P.parse_hourly_rate("180 zł netto (+VAT) / godz.")
    assert lo == 180 and hi is None


def test_monthly_salary_is_refused_rather_than_pro_rated():
    """A monthly contract figure cannot be pro-rated without a contracted weekly load."""
    lo, hi, how = P.parse_hourly_rate("23 500 zł netto (+VAT) / miesiąc")
    assert lo is None and "not an hourly rate" in how


def test_rate_parser_does_not_mistake_a_year_or_a_small_number_for_a_rate():
    lo, hi, _ = P.parse_hourly_rate("5 zł / godz.")
    assert lo is None, "a 5 zł/h figure is not a real rate and must not be reported"
