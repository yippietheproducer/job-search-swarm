"""Tests for the remote-claim verifier.

Evidence base: lane 8's audit of offers.db (2026-09-22) — two `remote=1` rows at
fit 95 whose posting bodies contradict the flag.
"""
import unittest

from pracuj_ai.remote_truth import contradicts_remote, verify_remote

# Quoted verbatim from the postings lane 8 audited.
ERYK_LAGOS = (
    "AI/IT Trainee Program (Remote). Location: Lagos, Nigeria. Approximately 80% "
    "of your work will be carried out from our service hub in Gbagada, Lagos."
)
CLOUDPLEXO_HYBRID = (
    "Global Graduate Trainee Program (Remote, Cloud/AI). The program uses a hybrid "
    "working setup, balancing focused in-office time with the flexibility to work remotely."
)
FILEN = "(20 hrs per week preferred · up to 40 hrs possible · 0-100% remote · flexible hours)"
WTF = "Fully Remote — Worldwide. Fully remote, async-first; we sync once a week."


class ConflictTest(unittest.TestCase):
    def test_hybrid_ad_is_caught_even_though_it_says_remote(self):
        verdict, evidence = verify_remote(CLOUDPLEXO_HYBRID)
        self.assertEqual(verdict, "HYBRID")
        self.assertIn("hybrid", evidence.lower())
        self.assertTrue(contradicts_remote(CLOUDPLEXO_HYBRID))

    def test_percentage_onsite_hub_is_caught(self):
        verdict, _ = verify_remote(ERYK_LAGOS)
        self.assertIn(verdict, ("ONSITE", "HYBRID"))
        self.assertTrue(contradicts_remote(ERYK_LAGOS))

    def test_onsite_days_per_week(self):
        self.assertTrue(contradicts_remote("Remote role. 3 days per week on-site required."))

    def test_explicit_onsite_position(self):
        self.assertTrue(contradicts_remote("This is an on-site position based in Munich."))


class ConfirmTest(unittest.TestCase):
    def test_fully_remote_confirms(self):
        self.assertEqual(verify_remote(WTF)[0], "REMOTE-CONFIRMED")

    def test_flexible_hours_ad_still_confirms_remote(self):
        self.assertEqual(verify_remote(FILEN)[0], "REMOTE-CONFIRMED")

    def test_polish_remote_phrasing_confirms(self):
        self.assertEqual(verify_remote("Praca w pełni zdalna z Warszawy.")[0],
                         "REMOTE-CONFIRMED")

    def test_conflict_beats_confirmation(self):
        """'hybrid ... with the flexibility to work remotely' must not confirm."""
        self.assertEqual(verify_remote(CLOUDPLEXO_HYBRID)[0], "HYBRID")


class UnknownTest(unittest.TestCase):
    def test_ambiguous_text_is_unknown_not_remote(self):
        self.assertEqual(verify_remote("Junior developer, competitive salary.")[0], "UNKNOWN")

    def test_empty_and_none_are_unknown(self):
        self.assertEqual(verify_remote("")[0], "UNKNOWN")
        self.assertEqual(verify_remote(None)[0], "UNKNOWN")
        self.assertFalse(contradicts_remote(None))

    def test_unknown_is_not_treated_as_contradiction(self):
        # UNKNOWN means 'not proven remote' — it must NOT be reported as a conflict,
        # or every posting without the word 'remote' would be flagged.
        self.assertFalse(contradicts_remote("We are hiring a Python developer."))


if __name__ == "__main__":
    unittest.main()
