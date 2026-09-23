"""Regression tests for the board fetchers (no network — payloads are faked).

These lock in the fix for a silent upstream bug found 2026-09-22: Remotive's API
ignores `search`, `category` and `limit`, returning the same 18 latest jobs for
every query. Verified externally by hashing the returned job-id set for five
different URLs (all identical). See `pracuj_ai/boards.py` module docstring.
"""
import unittest
from unittest import mock

from pracuj_ai import boards


def _remotive_payload():
    return {
        "jobs": [
            {
                "id": 1, "url": "https://remotive.com/jobs/react-one",
                "title": "Junior React Developer",
                "company_name": "React Co", "candidate_required_location": "EU",
                "tags": ["react", "typescript"], "description": "Build UI in React.",
                "category": "Software Development",
            },
            {
                "id": 2, "url": "https://remotive.com/jobs/writer-one",
                "title": "Content Writer",
                "company_name": "Words Inc", "candidate_required_location": "Anywhere",
                "tags": ["writing"], "description": "Write blog posts.",
                "category": "Marketing",
            },
        ]
    }


class RemotiveSearchIgnoredTest(unittest.TestCase):
    """`fetch_remotive` must filter client-side, because upstream will not."""

    def setUp(self):
        patcher = mock.patch.object(boards, "_get_json", return_value=_remotive_payload())
        self.get_json = patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_term_returns_everything(self):
        out = boards.fetch_remotive("")
        self.assertEqual(len(out), 2)
        self.assertEqual({o["title"] for o in out},
                         {"Junior React Developer", "Content Writer"})

    def test_term_filters_client_side(self):
        out = boards.fetch_remotive("react")
        self.assertEqual([o["title"] for o in out], ["Junior React Developer"])

    def test_term_matching_only_category_is_found(self):
        # `category` lives outside title/tags, so the haystack must include it.
        out = boards.fetch_remotive("marketing")
        self.assertEqual([o["title"] for o in out], ["Content Writer"])

    def test_term_absent_from_payload_yields_nothing(self):
        self.assertEqual(boards.fetch_remotive("rust-embedded"), [])

    def test_no_query_params_are_sent(self):
        """The API ignored them; sending them only multiplied identical calls."""
        boards.fetch_remotive("react")
        url = self.get_json.call_args[0][0]
        self.assertEqual(url, "https://remotive.com/api/remote-jobs")
        self.assertNotIn("search=", url)
        self.assertNotIn("limit=", url)

    def test_limit_is_applied_after_filtering(self):
        # limit=1 must not truncate before filtering, or a match could be lost.
        out = boards.fetch_remotive("writer", limit=1)
        self.assertEqual([o["title"] for o in out], ["Content Writer"])


class TermAgnosticSweepTest(unittest.TestCase):
    """A source that ignores the term must be fetched once, not once per keyword."""

    def test_remotive_is_fetched_once_for_many_keywords(self):
        calls = []

        def fake_fetch(term, limit=20):
            calls.append(term)
            return [{
                "id": "r1", "title": "Junior React Developer",
                "url": "https://remotive.com/jobs/react-one", "company": "React Co",
                "location": "EU", "remote": True, "tags": ["react"],
                "description": "React work.", "source": "remotive",
            }]

        with mock.patch.dict(boards._FETCHERS, {"remotive": fake_fetch}, clear=False), \
             mock.patch.object(boards, "SOURCES", ["remotive"]):
            out = boards.fetch_all(["react", "python", "node", "java"], limit=5)

        self.assertEqual(calls, [""], "must sweep once, not once per keyword")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Junior React Developer")

    def test_keyword_sources_still_sweep_per_keyword(self):
        calls = []

        def fake_fetch(term, limit=20):
            calls.append(term)
            return []

        with mock.patch.dict(boards._FETCHERS, {"remoteok": fake_fetch}, clear=False), \
             mock.patch.object(boards, "SOURCES", ["remoteok"]):
            boards.fetch_all(["react", "python"], limit=5)

        self.assertEqual(calls, ["react", "python"])


if __name__ == "__main__":
    unittest.main()
