import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("daily_brief", ROOT / "scripts" / "build_daily_brief.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MajorNewsTest(unittest.TestCase):
    def test_direct_guidance_headline_is_high_impact(self):
        item = {
            "uuid": "example",
            "title": "Arista Networks Raises Growth Outlook After Quarterly Results",
            "publisher": "Reuters",
            "link": "https://example.com/arista",
            "providerPublishTime": 1785960000,
            "relatedTickers": ["ANET"],
        }
        result = MODULE.classify_major_news_item(item, "ANET")
        self.assertEqual("Earnings / Guidance", result["category"])
        self.assertEqual(5, result["impact_score"])
        self.assertEqual("Positive", result["direction"])
        self.assertEqual("Direct", result["directness"])

    def test_untrusted_or_nonmaterial_headline_is_rejected(self):
        untrusted = {
            "title": "Nvidia could be the best stock ever",
            "publisher": "Unknown Blog",
            "link": "https://example.com/noise",
            "providerPublishTime": 1785960000,
            "relatedTickers": ["NVDA"],
        }
        self.assertIsNone(MODULE.classify_major_news_item(untrusted, "NVDA"))

    def test_related_ticker_tag_without_direct_event_is_rejected(self):
        roundup = {
            "title": "Jobs Report and Other Earnings: What to Watch the Rest of the Week",
            "publisher": "The Wall Street Journal",
            "link": "https://example.com/roundup",
            "providerPublishTime": 1785960000,
            "relatedTickers": ["DDOG"],
        }
        self.assertIsNone(MODULE.classify_major_news_item(roundup, "DDOG"))


if __name__ == "__main__":
    unittest.main()
