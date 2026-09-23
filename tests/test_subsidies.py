"""Unit tests for the funded-companies module (no network)."""
import json
import tempfile
import unittest

from pracuj_ai import subsidies


class SubsidiesTest(unittest.TestCase):
    def test_seeded_has_search_tokens(self):
        companies = subsidies.load_companies()
        self.assertGreater(len(companies), 10)
        for c in companies:
            self.assertIn("search", c)
            self.assertTrue(c["search"])

    def test_load_from_json(self):
        data = [
            {"name": "Firma A", "note": "PARP", "search": "FirmaA"},
            {"name": "Firma B", "search": "FirmaB"},
        ]
        fd, path = tempfile.mkstemp(suffix=".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        companies = subsidies.load_companies(path)
        self.assertEqual(len(companies), 2)
        self.assertEqual(companies[0]["note"], "PARP")
        self.assertEqual(companies[1]["name"], "Firma B")
        # note defaults to "" when missing
        self.assertEqual(companies[1]["note"], "")
        import os
        os.unlink(path)

    def test_string_entries_normalized(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(["SoloFirma"], fh)
        companies = subsidies.load_companies(path)
        self.assertEqual(companies[0]["name"], "SoloFirma")
        self.assertEqual(companies[0]["search"], "SoloFirma")
        import os
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
