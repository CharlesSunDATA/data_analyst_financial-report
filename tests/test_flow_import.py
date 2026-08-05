import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "flow_importer", ROOT / "scripts" / "import_etf_global_flows.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FlowImportTest(unittest.TestCase):
    def test_normalize_filters_and_deduplicates(self):
        sample = (
            "composite_ticker,effective_date,fund_flow,processed_date\n"
            "SMH,2026-08-01,1000000,2026-08-02T00:00:00Z\n"
            "SMH,2026-08-01,1250000,2026-08-02T01:00:00Z\n"
            "SOXX,08/02/2026,\"($500,000)\",2026-08-03T00:00:00Z\n"
            "QQQ,2026-08-02,9000000,2026-08-03T00:00:00Z\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.csv"
            path.write_text(sample, encoding="utf-8")
            normalized = MODULE.normalize(path)
        self.assertEqual(1, len(normalized["SMH"]))
        self.assertEqual("1250000.00", normalized["SMH"][0]["net_flow_usd"])
        self.assertEqual("-500000.00", normalized["SOXX"][0]["net_flow_usd"])


if __name__ == "__main__":
    unittest.main()
