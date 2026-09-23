"""Unit tests for the SQLite offer store (funded flag, dedup, filters)."""
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from pracuj_ai.store import OfferRecord, Store, offer_id


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def _rec(self, oid="1001", funded=False, fit=None, company="ACME"):
        return OfferRecord(
            id=oid, title="Dev", url=f"https://pracuj.pl/praca/dev,oferta,{oid}",
            company=company, funded=funded, fit_score=fit,
        )

    def test_offer_id_from_url(self):
        self.assertEqual(offer_id("https://www.pracuj.pl/praca/x,oferta,12345?s=a"), "12345")
        import hashlib
        self.assertEqual(offer_id("no id here"),
                         "x" + hashlib.md5(b"no id here").hexdigest()[:16])

    def test_upsert_new_and_dedup(self):
        self.assertTrue(self.store.upsert(self._rec("1", funded=True, fit=80)))
        # same id again -> not new, fit preserved
        self.assertFalse(self.store.upsert(self._rec("1", funded=True, fit=80)))
        self.assertTrue(self.store.exists("1"))

    def test_funded_filter_and_stats(self):
        self.store.upsert(self._rec("1", funded=True, fit=80))
        self.store.upsert(self._rec("2", funded=False, fit=20))
        funded = self.store.list_offers(funded_only=True)
        self.assertEqual(len(funded), 1)
        self.assertEqual(funded[0].id, "1")
        self.assertEqual(self.store.stats()["funded"], 1)

    def test_min_fit_filter(self):
        self.store.upsert(self._rec("1", fit=10))
        self.store.upsert(self._rec("2", fit=90))
        self.assertEqual(len(self.store.list_offers(min_fit=50)), 1)

    def test_since_filter(self):
        old = self._rec("1", fit=70)
        old.first_seen = "2020-01-01T00:00:00+00:00"
        self.store.upsert(old)
        new = self._rec("2", fit=70)
        new.first_seen = "2099-01-01T00:00:00+00:00"
        self.store.upsert(new)
        recent = self.store.list_offers(since="2099-01-01T00:00:00+00:00")
        self.assertEqual([r.id for r in recent], ["2"])

    def test_migration_is_idempotent(self):
        # Re-opening the same db (old schema simulated by re-running init) must not error.
        s2 = Store(self.path)
        s2.upsert(self._rec("3", funded=True))
        self.assertEqual(s2.stats()["funded"], 1)
        s2.close()


if __name__ == "__main__":
    unittest.main()


def test_offer_id_fallback_is_stable_across_processes(tmp_path):
    """The non-pracuj fallback used builtin hash(), which is salted per process
    (PYTHONHASHSEED): the same URL minted a different id in every run, so the
    dedup/new-detection the store exists for silently broke across monitor
    cycles. Pin the stable derivation."""
    import hashlib
    import subprocess
    import sys as _sys

    url = "https://example.com/job/123"  # no ',oferta,<id>' -> fallback path
    expected = "x" + hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    assert offer_id(url) == expected
    out = subprocess.check_output(
        [_sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); from pracuj_ai.store import offer_id;"
         " print(offer_id(%r))" % (str(ROOT), url)], text=True).strip()
    assert out == expected, "offer_id must not depend on per-process hash salt"
