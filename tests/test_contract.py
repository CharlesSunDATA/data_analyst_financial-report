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
            "validation", "alpha_timeline", "sources",
        }
        self.assertTrue(required.issubset(self.data))
        self.assertEqual(16, len(self.data["watchlist"]))
        self.assertEqual(8, len(self.data["sectors"]))

    def test_unavailable_flow_is_not_fabricated(self):
        for etf in ("SMH", "SOXX"):
            flow = self.data["flows"][etf]
            if flow["status"] != "available":
                self.assertEqual([], flow["rows"])
                self.assertTrue(all(value is None for value in flow["windows"].values()))
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


if __name__ == "__main__":
    unittest.main()
