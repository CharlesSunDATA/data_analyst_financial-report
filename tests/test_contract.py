import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DailyBriefContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))

    def test_required_sections_and_universe(self):
        required = {
            "metadata", "executive", "market", "flows", "sectors", "watchlist",
            "divergences", "institutional_money_flow", "catalysts", "pm_actions",
            "validation", "alpha_timeline", "major_news", "sources",
        }
        self.assertTrue(required.issubset(self.data))
        self.assertEqual(16, len(self.data["watchlist"]))
        self.assertEqual(8, len(self.data["sectors"]))

    def test_unavailable_flow_is_not_fabricated(self):
        for etf in ("SMH", "SOXX"):
            flow = self.data["flows"][etf]
            self.assertIn("confirmed_events", flow)
            self.assertIn("feed_setup", flow)
            if flow["status"] != "available":
                self.assertEqual([], flow["rows"])
                self.assertTrue(all(value is None for value in flow["windows"].values()))
                self.assertEqual("subscription_required", flow["feed_setup"]["status"])
            for event in flow["confirmed_events"]:
                self.assertIn("Sparse", event["coverage"])
        money_flow = self.data["institutional_money_flow"]
        self.assertIn("daily_public_proxies", money_flow)
        self.assertIn("official_delayed", money_flow)
        self.assertIn("proprietary_unavailable", money_flow)
        self.assertEqual(
            "credentials_required",
            money_flow["official_delayed"]["finra_ats"]["status"],
        )
        self.assertEqual(
            "data_unavailable",
            money_flow["proprietary_unavailable"]["prime_broker_positioning"]["status"],
        )
        self.assertIn(
            money_flow["daily_public_proxies"]["put_call"]["status"],
            {"available", "pending"},
        )

    def test_dates_and_archives(self):
        report_date = self.data["metadata"]["report_date"]
        self.assertTrue(self.data["metadata"]["market_data_as_of"])
        self.assertTrue((ROOT / "reports" / f"{report_date}.html").exists())
        self.assertTrue((ROOT / "data" / "daily" / f"{report_date}.json").exists())
        self.assertTrue((ROOT / "latest" / "index.html").exists())

    def test_fact_interpretation_contract(self):
        self.assertIn("score_components", self.data["executive"])
        for component in self.data["executive"]["score_components"]:
            self.assertIn("weight", component)
            self.assertIn("detail", component)
        for stock in self.data["watchlist"]:
            if stock.get("status") == "available":
                self.assertLess(stock["score_coverage"], 100)
                self.assertIn("excluded", stock["score_note"])

    def test_page_two_is_fund_flow_only(self):
        app = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn("PAGE 02 · ETF FUND FLOW", app)
        self.assertIn("VanEck Semiconductor ETF", app)
        self.assertIn("iShares Semiconductor ETF · SOX代理", app)
        self.assertNotIn("SMH Price ·", app)
        self.assertNotIn("Price vs ETF Flow", app)

    def test_major_news_is_low_noise_and_explicit(self):
        feed = self.data["major_news"]
        self.assertLessEqual(len(feed["items"]), 6)
        self.assertIn("not a price forecast", feed["limitations"])
        for item in feed["items"]:
            self.assertIn(item["impact_score"], {3, 4, 5})
            self.assertIn(item["direction"], {"Positive", "Negative", "Mixed"})
            self.assertTrue(item["affected_tickers"])
            self.assertTrue(item["source_url"].startswith("http"))
            self.assertIn("Headline verified", item["evidence_status"])


if __name__ == "__main__":
    unittest.main()
