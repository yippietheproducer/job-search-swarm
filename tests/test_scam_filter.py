"""Tests for the deterministic scam filter (regression-locks the 2026-09-22 audit).

Evidence base: research/remote_parttime/SOURCE_RELIABILITY.md and the orchestrator
audit of offers.db — the highest-scored rows (fit 92-95) were mostly ad-farm
templates on free-hosting subdomains, while real employers scored lower (88).
"""
import unittest

from pracuj_ai.scam_filter import FREE_HOST_RE, is_suspect, is_thin

# Real text captured from offers.db (HireSub, fit 95).
HIRESUB_TEMPLATE = (
    "Junior Software Developer at HireSub, fully remote. Join a small, "
    "fast-moving engineering team building a consumer-facing web application. "
    "Flexible title, time, and commitment (full-time, part-time, side-gig, or "
    "contract). No degree required. Early-career developers welcome."
)


class DomainLayerTest(unittest.TestCase):
    def test_verified_farm_domains_are_flagged(self):
        for url in (
            "https://flexgen.zya.me/job/junior-frontend-developer-2",
            "https://workflowza.zya.me/job/junior-software-engineer-remote",
            "https://hiresub.infinityfree.me/job/junior-software-developer-remote",
            "https://worksynergy.10001mb.com/remote-jobs/junior-full-stack-engineer",
            "https://vacancies.is-great.net/",
            "https://brighthush.8r.unaux.com/",
            "https://joblume.totalh.net/",
            "https://remoteforge.2kool4u.net/remote-jobs/junior-backend-developer",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_suspect(url), url)

    def test_new_subdomain_on_known_free_host_is_flagged(self):
        """The generic pattern must catch farms before a human audits them."""
        for url in (
            "https://totally-new-company.zya.me/job/x",
            "https://another-one.10001mb.com/remote-jobs/y",
            "https://fresh.is-great.net/",
            "https://x.000webhostapp.com/jobs",
            "https://brand-new.2kool4u.net/remote-jobs/y",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_suspect(url), url)

    def test_real_employers_are_not_flagged(self):
        for url in (
            "https://careers.ey.com/ey/job/Warsaw-Junior-AI-Engineer",
            "https://www.dynatrace.com/careers/",
            "https://careers.sdworx.com/",
            "https://jobs.smartrecruiters.com/Docplanner/",
            "https://it.pracuj.pl/praca/frontend;kw",
            "https://justjoin.it/job-offer/link-group-junior-python-developer-warszawa",
            "https://costmine.com/careers/back-end-developer",
            "https://career.sigma.software/jobs/",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_suspect(url), url)

    def test_free_host_regex_does_not_match_lookalike_domains(self):
        # A real company domain must never match just because it contains a
        # similar substring (e.g. "is-great.net" inside "this-great.network").
        for url in (
            "https://this-great.network/jobs",
            "https://myzya.me.example.com/",
            "https://unaux.competitor.io/",
        ):
            with self.subTest(url=url):
                self.assertFalse(FREE_HOST_RE.search(url) is not None, url)


class TitleAndTextLayerTest(unittest.TestCase):
    def test_bait_title_is_flagged(self):
        self.assertTrue(is_suspect("https://example.com/jobs/1",
                                   "Junior Developer (Remote, no degree required)"))

    def test_template_text_on_thin_ad_is_flagged(self):
        self.assertTrue(is_suspect("https://example.com/jobs/2", "Junior Dev",
                                   HIRESUB_TEMPLATE))

    def test_same_phrase_in_a_long_real_ad_is_not_flagged(self):
        """A 2500-char ad that merely contains the phrase is not a farm stub."""
        long_ad = HIRESUB_TEMPLATE + " " + ("We build a Next.js product on AWS with "
                                            "PostgreSQL and Terraform. ") * 60
        self.assertGreater(len(long_ad), 300)
        self.assertFalse(is_suspect("https://example.com/jobs/3", "Junior Dev", long_ad))

    def test_backward_compatible_two_arg_call(self):
        # Existing caller scripts/append_new_offers.py uses (url, title) only.
        self.assertTrue(is_suspect("https://flexgen.zya.me/job/x", "Junior Dev"))
        self.assertFalse(is_suspect("https://careers.ey.com/ey/job/1", "Junior Dev"))


class ThinTextTest(unittest.TestCase):
    def test_template_stub_is_thin(self):
        self.assertTrue(is_thin("https://hiresub.infinityfree.me/x", HIRESUB_TEMPLATE))

    def test_short_but_technical_ad_is_not_thin(self):
        self.assertFalse(is_thin("https://x.com/1",
                                 "Remote part-time. Stack: React, TypeScript, Node."))

    def test_long_ad_is_not_thin(self):
        self.assertFalse(is_thin("https://x.com/2", "Python Django " * 40))


if __name__ == "__main__":
    unittest.main()
