"""Tests for deterministic evidence scoring.

The decisive test is `test_farm_stub_scores_far_below_real_posting`: the farm ad
that topped the ranking carries a high skill-match but almost no evidence, and
the two real survivors carry a full evidence set.
"""
import unittest

from pracuj_ai.evidence import (
    ATTRS, actionable_verdict, detect, eligibility, parse_hours,
    part_time_hint, score_evidence,
)

# --- the ad-farm stub that ranked fit=95 and dominated the top of offers.db ---
HIRESUB_FARM = (
    "Junior Software Developer at HireSub, fully remote. Join a small, fast-moving "
    "engineering team building a consumer-facing web application. Flexible title, time, "
    "and commitment (full-time, part-time, side-gig, or contract). No degree required. "
    "Early-career developers welcome."
)
# --- verified real posting #1 (employer's own ATS) ---
PRODUCTIV = (
    "Junior Full-Stack Developer (AI-Assisted) Remote Part Time Entry Level. We are looking "
    "for a Junior Full-Stack Developer to join a small team building a TypeScript product on "
    "Next.js 15 and React 19, with an AI pipeline powered by Anthropic Claude, real-time "
    "collaboration, and background job processing on Google Cloud Run. Comfort with SQL and an "
    "ORM. A testing habit: writes unit tests without being asked. This is a part-time "
    "opportunity. Are you willing to work part-time (20 hrs)?"
)
# --- verified real posting #2 ---
FILEN = (
    "Frontend Developer - Web Platform (React + Next.js). 20 hrs per week preferred, up to 40 "
    "hrs possible. Fully remote within the EU. EUR 18-27/h. Employee status, no subcontractors. "
    "Stack: TypeScript, React, Next.js, Tailwind, GraphQL."
)


class HoursTest(unittest.TestCase):
    def test_numeric_hours_extracted(self):
        self.assertEqual(parse_hours("20 hrs per week preferred")[0], 20)
        self.assertEqual(parse_hours("up to 30hrs/week")[0], 30)
        self.assertEqual(parse_hours("praca 20 godzin tygodniowo")[0], 20)

    def test_fte_converted_to_hours(self):
        self.assertEqual(parse_hours("0.5 FTE")[0], 20)
        self.assertEqual(parse_hours("1/2 etatu")[0], 20)

    def test_bare_part_time_mention_is_not_hours_evidence(self):
        """Decisive: the farm says 'part-time' without any figure."""
        hours, quote = parse_hours(HIRESUB_FARM)
        self.assertIsNone(hours, "a part-time mention with no number is not hours evidence")
        self.assertIsNone(quote)

    def test_part_time_hint_is_reported_but_separate(self):
        self.assertIsNotNone(part_time_hint(HIRESUB_FARM))
        self.assertIsNone(part_time_hint(FILEN))

    def test_baseline_not_maximum_is_taken(self):
        """Real bug: '20 hrs preferred, up to 40 possible' was read as 40 and wrongly
        failed the part-time cap. The committed baseline is 20."""
        hours, quote = parse_hours(FILEN)
        self.assertEqual(hours, 20, f"got {hours} from {quote!r}")
        self.assertTrue(eligibility(FILEN, domain="filen.io")["part_time"])

    def test_no_hours_in_plain_text(self):
        self.assertEqual(parse_hours("We are a fast growing startup.")[0], None)


class EvidenceScoreTest(unittest.TestCase):
    def test_farm_stub_scores_far_below_real_posting(self):
        farm, farm_present, _ = score_evidence(HIRESUB_FARM, domain="hiresub.infinityfree.me")
        real, _, _ = score_evidence(FILEN, domain="filen.io")
        self.assertLess(farm, 30, f"farm stub scored {farm}: {farm_present}")
        self.assertGreaterEqual(real, 70, f"real posting scored {real}")
        self.assertGreater(real - farm, 40, "separation must be decisive")

    def test_measured_separation_on_the_three_real_texts(self):
        """Documented calibration: farm 29, sparse-but-real ATS 57, full posting 86.

        Note the farm is NOT zero, and that is honest: it legitimately earns
        `remote` ('fully remote') and `level` ('Junior') because it is *engineered*
        to match a ranker. It fails on every substantive attribute — hours, rate,
        geo, stack, employer — which is exactly what the score is for. A detector
        that scored it 0 would be over-fitting.
        """
        farm = score_evidence(HIRESUB_FARM, domain="hiresub.infinityfree.me")[0]
        ats = score_evidence(PRODUCTIV, domain="jobs.productiv.team")[0]
        full = score_evidence(FILEN, domain="filen.io")[0]
        self.assertEqual((farm, ats, full), (29, 57, 86))
        self.assertLess(farm, ats)
        self.assertLess(ats, full)
        self.assertLess(farm, 50, "must fall below the evidence floor")

    def test_farm_stub_lacks_the_decision_attributes(self):
        _, present, _ = score_evidence(HIRESUB_FARM)
        for attr in ("hours", "rate", "geo", "stack", "employer"):
            self.assertIsNone(present[attr], f"farm stub must not have {attr} evidence")

    def test_farm_cannot_fake_evidence_by_keyword_stuffing(self):
        """Stuffing a stub full of buzzwords must not manufacture hours/rate."""
        stuffed = HIRESUB_FARM + " Python TypeScript React Next.js Django SQL Docker AWS"
        _, present, _ = score_evidence(stuffed)
        self.assertIsNone(present["hours"])
        self.assertIsNone(present["rate"])
        self.assertIsNone(present["employer"])

    def test_real_posting_has_hours_and_rate(self):
        _, present, _ = score_evidence(FILEN)
        self.assertTrue(present["hours"])
        self.assertTrue(present["rate"])
        self.assertTrue(present["geo"])
        self.assertTrue(present["stack"])

    def test_score_is_bounded_and_deterministic(self):
        first = score_evidence(PRODUCTIV)
        self.assertEqual(first, score_evidence(PRODUCTIV))
        self.assertTrue(0 <= first[0] <= 100)

    def test_empty_text_scores_zero(self):
        score, _, missing = score_evidence("")
        self.assertEqual(score, 0)
        self.assertEqual(missing, list(ATTRS))

    def test_employer_credited_only_for_resolvable_domain(self):
        score_no_domain, present, missing = score_evidence(PRODUCTIV)
        self.assertIsNone(present["employer"])
        self.assertIn("employer", missing)
        score_domain, present2, _ = score_evidence(PRODUCTIV, domain="jobs.productiv.team")
        self.assertEqual(present2["employer"], "jobs.productiv.team")
        self.assertGreater(score_domain, score_no_domain)
        # a board URL earns nothing, even though the posting names an employer
        _, board, _ = score_evidence(PRODUCTIV, domain="pracuj.pl")
        self.assertIsNone(board["employer"])

    def test_monthly_rate_is_not_mistaken_for_monthly_hours(self):
        """'140-180 zl/h' must not be read as an hours figure."""
        hours, _ = parse_hours("AI Developer. Rate: 140-180 zl/h netto+VAT B2B. 20h/week.")
        self.assertEqual(hours, 20)

    def test_employer_domain_credited_but_board_domain_is_not(self):
        _, ok, _ = score_evidence(FILEN, domain="filen.io")
        self.assertEqual(ok["employer"], "filen.io")
        _, no, missing = score_evidence(FILEN, domain="pracuj.pl")
        self.assertIsNone(no["employer"])
        self.assertIn("employer", missing)

    def test_free_host_domain_is_not_credited_as_employer(self):
        _, present, missing = score_evidence(HIRESUB_FARM, domain="hiresub.infinityfree.me")
        self.assertIsNone(present["employer"])
        self.assertIn("employer", missing)


class EligibilityTest(unittest.TestCase):
    def test_farm_fails_part_time_gate(self):
        e = eligibility(HIRESUB_FARM, domain="hiresub.infinityfree.me")
        self.assertFalse(e["part_time"], e["reasons"])
        self.assertFalse(e["all_pass"])

    def test_real_part_time_remote_row_passes(self):
        e = eligibility(FILEN, domain="filen.io")
        self.assertTrue(e["part_time"])
        self.assertTrue(e["remote"])
        self.assertTrue(e["all_pass"], e["reasons"])
        self.assertEqual(e["hours_per_week"], 20)

    def test_full_time_row_fails_the_hours_cap(self):
        e = eligibility("Remote role. 40 hrs per week. Fully remote, worldwide.")
        self.assertFalse(e["part_time"])
        self.assertTrue(any("exceeds" in r for r in e["reasons"]))

    def test_hybrid_rows_are_not_remote_confirmed(self):
        e = eligibility("hybrid working setup, balancing focused in-office time with 20 hrs per week")
        self.assertFalse(e["remote"])
        self.assertEqual(e["remote_verdict"], "HYBRID")

    def test_senior_only_gate_fails(self):
        e = eligibility("Senior Full-Stack Engineer. Fully remote. 20 hrs per week.")
        self.assertFalse(e["senior_ok"])
        self.assertFalse(e["all_pass"])

    def test_common_word_regular_does_not_defeat_the_senior_gate(self):
        """Regression, found on live offers.db: the level alternation included
        'regular', which is an ordinary English word. "regular meetings" then
        matched as an explicit level and cancelled the senior-only gate, letting
        a fit=5 senior mobile role reach SEND."""
        e = eligibility(
            "Senior Mobile Full Stack Developer. Fully remote. 20 hrs per week. "
            "You will join regular meetings and our regular release cadence."
        )
        self.assertTrue(e["senior_ok"] is False, e["reasons"])
        self.assertFalse(e["all_pass"])

    def test_polish_mid_level_still_counts_as_explicit(self):
        e = eligibility("Mid-level developer, średniozaawansowany. Fully remote. 20 h/week.")
        self.assertTrue(e["senior_ok"])

    def test_missing_domain_blocks_all_pass(self):
        e = eligibility(FILEN, domain=None)
        self.assertFalse(e["all_pass"])
        self.assertTrue(any("employer domain unproven" in r for r in e["reasons"]), e["reasons"])

    def test_board_url_is_not_employer_evidence(self):
        """A marketplace URL must never satisfy the employer gate, or every row on
        a board passes it for free and the gate means nothing."""
        e = eligibility(FILEN, domain="pracuj.pl")
        self.assertFalse(e["has_employer"])
        self.assertFalse(e["all_pass"])
        for board in ("www.pracuj.pl", "nofluffjobs.com", "jobgether.com", "justjoin.it"):
            self.assertFalse(eligibility(FILEN, domain=board)["has_employer"], board)

    def test_company_NAME_is_not_employer_evidence(self):
        """Regression, measured on live data. Crediting `offers.company` raised the
        mean evidence of the 14 farm rows from 27.8 to 42.0 and erased the gap
        against real rows (41.6) - the farm names 'HireSub'/'Workflowza'/'Flexgen'
        too. A name any bot can type is not evidence; only a resolvable
        employer-owned domain is."""
        e = eligibility(FILEN, domain="pracuj.pl")
        self.assertFalse(e["has_employer"])
        self.assertFalse(e["all_pass"])
        self.assertTrue(any("employer domain unproven" in r for r in e["reasons"]), e["reasons"])

    def test_polish_monthly_capacity_is_converted_to_weekly(self):
        """Regression, found on live offers.db data: '40-60h/msc' (hours per MONTH)
        was unparsed, so a genuinely part-time role read as 'no hours stated'."""
        hours, quote = parse_hours("Capacity: Part-time (40-60h/msc), long-term")
        self.assertIsNotNone(hours, "monthly capacity must be parsed")
        self.assertAlmostEqual(hours, 40 / 4.33, places=2)
        self.assertIn("msc", quote)
        self.assertTrue(eligibility("100% remote. Capacity: Part-time (40-60h/msc)",
                                    domain="kyotu.pl")["part_time"])
        self.assertEqual(parse_hours("20 godzin/miesiac")[0], 20 / 4.33)


class VerdictTest(unittest.TestCase):
    def test_farm_is_never_send_even_with_perfect_skill_fit(self):
        farm_ev, _, _ = score_evidence(HIRESUB_FARM, domain="hiresub.infinityfree.me")
        e = eligibility(HIRESUB_FARM, domain="hiresub.infinityfree.me")
        self.assertNotEqual(actionable_verdict(100, farm_ev, e), "SEND")

    def test_real_row_with_evidence_is_send(self):
        ev, _, _ = score_evidence(FILEN, domain="filen.io")
        e = eligibility(FILEN, domain="filen.io")
        self.assertEqual(actionable_verdict(92, ev, e), "SEND")

    def test_good_match_but_unproven_gate_is_verify_first_not_reject(self):
        """The whole point of the third bucket: a strong skill match with an
        unproven gate is a verification task, not a rejection."""
        e = eligibility(FILEN, domain=None)
        self.assertEqual(actionable_verdict(92, 100, e), "VERIFY_FIRST")

    def test_high_skill_fit_cannot_override_thin_evidence(self):
        farm_ev, _, _ = score_evidence(HIRESUB_FARM, domain=None)
        e = eligibility(FILEN, domain="filen.io")  # gates pass...
        self.assertEqual(actionable_verdict(100, farm_ev, e), "VERIFY_FIRST")

    def test_low_skill_fit_rejects_even_when_all_gates_pass(self):
        """Regression, found on live offers.db: without a fit floor, a senior role
        scoring fit=5 reached SEND just by passing the hours/remote gates."""
        ev, _, _ = score_evidence(FILEN, domain="filen.io")
        e = eligibility(FILEN, domain="filen.io")
        self.assertTrue(e["all_pass"])
        self.assertEqual(actionable_verdict(5, ev, e), "REJECT")
        self.assertEqual(actionable_verdict(59, ev, e), "REJECT")
        self.assertEqual(actionable_verdict(60, ev, e), "SEND")

    def test_gates_and_evidence_can_only_demote_never_promote(self):
        """Monotonicity: raising evidence or fixing gates must never flip a
        rejected low-match row into SEND."""
        weak_gates = eligibility(FILEN, domain=None)
        strong_gates = eligibility(FILEN, domain="filen.io")
        self.assertEqual(actionable_verdict(30, 0, weak_gates), "REJECT")
        self.assertEqual(actionable_verdict(30, 100, strong_gates), "REJECT")

    def test_low_match_failing_gate_is_reject(self):
        e = eligibility(FILEN, domain=None)
        self.assertEqual(actionable_verdict(20, 50, e), "REJECT")


if __name__ == "__main__":
    unittest.main()
