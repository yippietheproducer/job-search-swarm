"""Unit tests for browser offer parsing (boilerplate filter, remote detection)."""
import unittest

from pracuj_ai.browser import _parse_offers
from pracuj_ai.monitor import detect_remote
from pracuj_ai.store import offer_id

_SNAPSHOT = """
text "Przejdź do treści ogłoszenia"
url=https://www.pracuj.pl/praca/senior-fullstack-warszawa,oferta,1004983067?s=abc)
text "Senior Fullstack Warszawa"
url=https://www.pracuj.pl/praca/boilerplate-offer,oferta,999999?sug=list_bd_1_boosterAI_L0&s=x)
text "Boilerplate Offer (sugestia)"
url=https://www.pracuj.pl/praca/remote-role,oferta,1005000000?s=zz)
text "Praca zdalna dla developera"
"""


class ParseTest(unittest.TestCase):
    def test_boilerplate_dropped(self):
        offers = _parse_offers(_SNAPSHOT, with_context=True)
        ids = {offer_id(o["url"]) for o in offers}
        self.assertIn("1004983067", ids)
        self.assertIn("1005000000", ids)
        # the sug= suggestion must be filtered out
        self.assertNotIn("999999", ids)
        self.assertEqual(len(offers), 2)

    def test_remote_detection_from_card(self):
        offers = _parse_offers(_SNAPSHOT, with_context=True)
        remote = [o for o in offers if o["remote"]]
        self.assertTrue(any("1005000000" in o["url"] for o in remote))

    def test_offer_id(self):
        self.assertEqual(offer_id("https://www.pracuj.pl/praca/x,oferta,555?s=a"), "555")

    def test_detect_remote_text(self):
        self.assertTrue(detect_remote("Oferta pracy zdalnej dla Ciebie"))
        self.assertFalse(detect_remote("Praca stacjonarna w biurze"))


if __name__ == "__main__":
    unittest.main()
