#!/usr/bin/env python3
"""Normalize ETF Global/AWS daily fund-flow CSV exports for the dashboard."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"SMH", "SOXX"}
SOURCE = "ETF Global via AWS Data Exchange"
SOURCE_URL = "https://aws.amazon.com/marketplace/pp/prodview-zwx5mkzazfpsa"
ALIASES = {
    "ticker": ("ticker", "symbol", "composite_ticker", "fund_ticker"),
    "date": ("date", "effective_date", "as_of_date", "data_date"),
    "flow": ("fund_flow", "net_flow", "net_flow_usd", "daily_fund_flow", "flow_usd"),
    "updated": ("processed_date", "updated_at", "last_updated", "publish_date"),
}


def pick(row: dict[str, str], key: str) -> str:
    lowered = {name.strip().lower(): value for name, value in row.items() if name}
    for alias in ALIASES[key]:
        value = lowered.get(alias)
        if value not in (None, ""):
            return value.strip()
    return ""


def parse_date(value: str) -> str:
    candidate = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        for fmt in ("%m/%d/%Y", "%Y%m%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    raise ValueError(f"Unsupported date: {value!r}")


def parse_number(value: str) -> float:
    cleaned = value.replace("$", "").replace(",", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    return float(cleaned)


def normalize(input_path: Path) -> dict[str, list[dict[str, str]]]:
    output = {ticker: [] for ticker in TARGETS}
    with input_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header.")
        for row in reader:
            ticker = pick(row, "ticker").upper()
            if ticker not in TARGETS:
                continue
            date = parse_date(pick(row, "date"))
            flow = parse_number(pick(row, "flow"))
            updated = pick(row, "updated") or datetime.now(timezone.utc).isoformat()
            output[ticker].append({
                "date": date,
                "net_flow_usd": f"{flow:.2f}",
                "source": SOURCE,
                "source_url": SOURCE_URL,
                "updated_at": updated,
            })
    for ticker, rows in output.items():
        deduped = {row["date"]: row for row in rows}
        output[ticker] = [deduped[date] for date in sorted(deduped)]
    return output


def write_outputs(normalized: dict[str, list[dict[str, str]]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = ("date", "net_flow_usd", "source", "source_url", "updated_at")
    for ticker, rows in normalized.items():
        if not rows:
            continue
        path = output_dir / f"{ticker.lower()}_flow.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print(f"{ticker}: wrote {len(rows)} rows to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="ETF Global fund-flow CSV export")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "flows")
    parser.add_argument("--check", action="store_true", help="Validate and summarize without writing")
    args = parser.parse_args()
    normalized = normalize(args.input)
    counts = {ticker: len(rows) for ticker, rows in normalized.items()}
    if not any(counts.values()):
        raise SystemExit("No SMH or SOXX rows found. Check the export columns and symbols.")
    if args.check:
        print(counts)
        return
    write_outputs(normalized, args.output_dir)


if __name__ == "__main__":
    main()
