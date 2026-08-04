#!/usr/bin/env python3
"""Build the Institutional AI Infrastructure Daily Research System.

The pipeline intentionally keeps unavailable institutional-flow datasets as
``pending``. Price/volume data is never relabelled as ETF, dark-pool, options,
or prime-broker flow.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
ADELAIDE = ZoneInfo("Australia/Adelaide")
NEW_YORK = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0 (compatible; InstitutionalAIResearch/1.0; +https://github.com/CharlesSunDATA/data_analyst_financial-report)"

WATCHLIST = [
    "NVDA", "AVGO", "AMD", "MU", "MRVL", "TSM", "ANET", "VRT",
    "LITE", "CIEN", "NET", "DDOG", "ORCL", "META", "NFLX", "SAP",
]

MARKET_SYMBOLS = {
    "QQQ": "QQQ",
    "SOX": "^SOX",
    "SMH": "SMH",
    "SOXX": "SOXX",
    "IWM": "IWM",
    "HYG": "HYG",
    "LQD": "LQD",
    "VIX": "^VIX",
    "VVIX": "^VVIX",
    "MOVE": "^MOVE",
    "DXY": "DX-Y.NYB",
    "USDJPY": "JPY=X",
    "WTI": "CL=F",
    "GOLD": "GC=F",
}

SECTOR_BASKETS = {
    "Networking": ["ANET", "MRVL"],
    "Power": ["VRT", "ETN", "POWI", "VICR"],
    "GPU": ["NVDA", "AMD", "AVGO"],
    "Optics": ["LITE", "CIEN", "COHR"],
    "Memory": ["MU", "WDC", "STX"],
    "Equipment": ["AMAT", "LRCX", "KLAC", "ASML"],
    "Software": ["NET", "DDOG", "ORCL", "SAP"],
    "Cooling": ["VRT", "NVT", "JCI"],
}

SOURCE_CATALOG = {
    "yahoo": {
        "name": "Yahoo Finance chart endpoint",
        "url": "https://finance.yahoo.com/",
        "tier": "B",
        "use": "Adjusted market price and volume history; cross-check with TradingView/Koyfin when decision-critical.",
    },
    "fred": {
        "name": "Federal Reserve Bank of St. Louis FRED",
        "url": "https://fred.stlouisfed.org/",
        "tier": "A",
        "use": "Official-source economic series distribution.",
    },
    "nasdaq": {
        "name": "Nasdaq earnings calendar",
        "url": "https://www.nasdaq.com/market-activity/earnings",
        "tier": "B",
        "use": "Upcoming earnings discovery; confirm material dates with company IR.",
    },
    "manual_official": {
        "name": "Curated official event sources",
        "url": "https://www.bls.gov/schedule/",
        "tier": "A",
        "use": "Fed/BLS/BEA and other official catalyst dates stored with source URL.",
    },
}


def http_json(url: str, timeout: int = 25) -> dict:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def yahoo_history(symbol: str, range_: str = "1y") -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?range={range_}&interval=1d&events=div%2Csplits"
    payload = http_json(url)
    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise ValueError(payload.get("chart", {}).get("error") or f"No Yahoo data for {symbol}")
    result = result[0]
    timestamps = result.get("timestamp") or []
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
    adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or quote_data.get("close") or []
    rows = []
    for idx, ts in enumerate(timestamps):
        close = adj[idx] if idx < len(adj) else None
        if close is None:
            continue
        volume = (quote_data.get("volume") or [])[idx] if idx < len(quote_data.get("volume") or []) else None
        rows.append({
            "date": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
            "close": round(float(close), 6),
            "volume": int(volume) if volume is not None else None,
        })
    if len(rows) < 2:
        raise ValueError(f"Insufficient Yahoo history for {symbol}")
    meta = result.get("meta", {})
    return {
        "symbol": symbol,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "rows": rows,
        "source_id": "yahoo",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def pct_change(rows: list[dict], sessions: int) -> float | None:
    if len(rows) <= sessions:
        return None
    old = rows[-sessions - 1]["close"]
    new = rows[-1]["close"]
    return round((new / old - 1) * 100, 3) if old else None


def ytd_change(rows: list[dict]) -> float | None:
    latest_year = rows[-1]["date"][:4]
    year_rows = [row for row in rows if row["date"].startswith(latest_year)]
    if len(year_rows) < 2:
        return None
    return round((year_rows[-1]["close"] / year_rows[0]["close"] - 1) * 100, 3)


def moving_average(rows: list[dict], n: int) -> float | None:
    if len(rows) < n:
        return None
    return round(statistics.fmean(row["close"] for row in rows[-n:]), 6)


def summarize_series(history: dict, key: str) -> dict:
    rows = history["rows"]
    last = rows[-1]
    ma20 = moving_average(rows, 20)
    ma50 = moving_average(rows, 50)
    trend = "盤整"
    if ma20 and ma50:
        if last["close"] > ma20 > ma50:
            trend = "上升"
        elif last["close"] < ma20 < ma50:
            trend = "下降"
    return {
        "key": key,
        "symbol": history["symbol"],
        "value": last["close"],
        "data_date": last["date"],
        "currency": history.get("currency"),
        "returns": {
            "1d": pct_change(rows, 1),
            "5d": pct_change(rows, 5),
            "20d": pct_change(rows, 20),
            "60d": pct_change(rows, 60),
            "ytd": ytd_change(rows),
        },
        "ma20": ma20,
        "ma50": ma50,
        "trend": trend,
        "history": rows,
        "source_id": history["source_id"],
        "fetched_at": history["fetched_at"],
    }


def load_flow_csv(etf: str) -> dict:
    path = ROOT / "data" / "flows" / f"{etf.lower()}_flow.csv"
    required = {"date", "net_flow_usd", "source", "source_url", "updated_at"}
    if not path.exists():
        return flow_pending(etf, "Validated historical flow file is not configured.")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            return flow_pending(etf, "Flow file schema is invalid.")
        rows = []
        for raw in reader:
            if not raw.get("date") or not raw.get("net_flow_usd"):
                continue
            rows.append({
                "date": raw["date"],
                "net_flow_usd": float(raw["net_flow_usd"]),
                "source": raw["source"],
                "source_url": raw["source_url"],
                "updated_at": raw["updated_at"],
            })
    rows.sort(key=lambda item: item["date"])
    if not rows:
        return flow_pending(etf, "No validated flow observations are available.")
    latest = rows[-1]
    values = [r["net_flow_usd"] for r in rows]
    windows = {}
    for label, length in (("daily", 1), ("5d", 5), ("20d", 20), ("60d", 60)):
        windows[label] = round(sum(values[-length:]), 2) if len(values) >= length else None
    current_year = latest["date"][:4]
    windows["ytd"] = round(sum(r["net_flow_usd"] for r in rows if r["date"].startswith(current_year)), 2)
    percentile = None
    if len(values) >= 60:
        percentile = round(sum(1 for value in values if value <= values[-1]) / len(values) * 100, 1)
    acceleration_5d = None
    if len(values) >= 10:
        acceleration_5d = round(statistics.fmean(values[-5:]) - statistics.fmean(values[-10:-5]), 2)
    return {
        "etf": etf,
        "status": "available",
        "message": "Validated source-backed ETF flow.",
        "data_date": latest["date"],
        "updated_at": latest["updated_at"],
        "source": latest["source"],
        "source_url": latest["source_url"],
        "windows": windows,
        "percentile_3y": percentile,
        "moving_average_20d": round(statistics.fmean(values[-20:]), 2) if len(values) >= 20 else None,
        "acceleration_5d": acceleration_5d,
        "flow_trend": "Accelerating" if acceleration_5d is not None and acceleration_5d > 0 else "Decelerating" if acceleration_5d is not None and acceleration_5d < 0 else "Pending",
        "rows": rows[-756:],
    }


def flow_pending(etf: str, reason: str) -> dict:
    return {
        "etf": etf,
        "status": "pending",
        "message": reason,
        "data_date": None,
        "updated_at": None,
        "source": None,
        "source_url": None,
        "windows": {"daily": None, "5d": None, "20d": None, "60d": None, "ytd": None},
        "percentile_3y": None,
        "moving_average_20d": None,
        "acceleration_5d": None,
        "flow_trend": "Pending",
        "rows": [],
    }


def safe_mean(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    return round(statistics.fmean(clean), 3) if clean else None


def build_sector_rankings(series: dict[str, dict]) -> list[dict]:
    qqq20 = series.get("QQQ", {}).get("returns", {}).get("20d") or 0
    ranked = []
    for name, members in SECTOR_BASKETS.items():
        available = [series[ticker] for ticker in members if ticker in series]
        r1 = safe_mean([item["returns"]["1d"] for item in available])
        r5 = safe_mean([item["returns"]["5d"] for item in available])
        r20 = safe_mean([item["returns"]["20d"] for item in available])
        rs20 = round(r20 - qqq20, 3) if r20 is not None else None
        breadth = round(sum(1 for item in available if item["ma20"] and item["value"] > item["ma20"]) / len(available) * 100, 1) if available else None
        score = 50
        if rs20 is not None:
            score += max(-25, min(25, rs20 * 3))
        if r5 is not None:
            score += max(-15, min(15, r5 * 2))
        if breadth is not None:
            score += (breadth - 50) * 0.2
        score = round(max(0, min(100, score)), 1)
        ranked.append({
            "name": name,
            "members": [item["key"] for item in available],
            "return_1d": r1,
            "return_5d": r5,
            "return_20d": r20,
            "relative_strength_20d": rs20,
            "breadth_above_20d": breadth,
            "score": score,
            "stars": max(1, min(5, round(score / 20))),
            "money_flow": "Pending — no validated segment-level institutional flow source.",
            "earnings_trend": "Pending — consensus revision feed not configured.",
            "risk": "High" if score < 35 else "Moderate" if score < 70 else "Crowding / reversal risk",
        })
    ranked.sort(key=lambda item: item["score"], reverse=True)
    for idx, item in enumerate(ranked, 1):
        item["rank"] = idx
    return ranked


def build_watchlist(series: dict[str, dict], sectors: list[dict]) -> list[dict]:
    sector_for = {}
    for sector in sectors:
        for ticker in sector["members"]:
            sector_for[ticker] = sector["name"]
    qqq20 = series.get("QQQ", {}).get("returns", {}).get("20d") or 0
    rows = []
    for ticker in WATCHLIST:
        item = series.get(ticker)
        if not item:
            rows.append({"ticker": ticker, "status": "pending", "message": "Market data unavailable."})
            continue
        rs = round((item["returns"]["20d"] or 0) - qqq20, 2)
        momentum = safe_mean([item["returns"]["5d"], item["returns"]["20d"]])
        alpha_score = round(max(0, min(100, 50 + rs * 3 + (momentum or 0) * 1.5)), 1)
        risk_score = round(max(0, min(100, 45 + abs(item["returns"]["20d"] or 0) * 1.8)), 1)
        rating = "Overweight" if alpha_score >= 70 else "Underweight" if alpha_score <= 35 else "Neutral"
        rows.append({
            "ticker": ticker,
            "status": "available",
            "sector": sector_for.get(ticker, "Platform / Other"),
            "price": item["value"],
            "data_date": item["data_date"],
            "trend": item["trend"],
            "returns": item["returns"],
            "relative_strength_20d": rs,
            "momentum": momentum,
            "money_flow": "Pending",
            "catalyst": "See 14-day catalyst calendar",
            "valuation": "Pending — consensus valuation feed not configured.",
            "alpha_score": alpha_score,
            "risk_score": risk_score,
            "pm_rating": rating,
            "score_coverage": 55,
            "score_note": "Price trend and relative strength only; institutional flow, estimate revisions and valuation are excluded until sourced.",
        })
    return rows


def fetch_nasdaq_earnings(start: date, days: int = 14) -> list[dict]:
    watch = set(WATCHLIST)
    dates = [start + timedelta(days=offset) for offset in range(days + 1)]
    dates = [current for current in dates if current.weekday() < 5]

    def fetch_day(current: date) -> list[dict]:
        url = f"https://api.nasdaq.com/api/calendar/earnings?date={current.isoformat()}"
        try:
            req = Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nasdaq.com/market-activity/earnings",
            })
            with urlopen(req, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = (((payload.get("data") or {}).get("rows")) or [])
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
            return []
        result = []
        for row in rows:
            ticker = (row.get("symbol") or "").upper()
            if ticker not in watch:
                continue
            result.append({
                "date": current.isoformat(),
                "time": row.get("time") or "Time not confirmed",
                "type": "Earnings",
                "title": f"{ticker} earnings",
                "ticker": ticker,
                "importance": 5 if ticker in {"NVDA", "AVGO", "AMD", "META"} else 4,
                "status": "discovered",
                "source_id": "nasdaq",
                "source_url": "https://www.nasdaq.com/market-activity/earnings",
                "note": "Confirm with company IR before trading.",
            })
        return result

    events = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fetch_day, current) for current in dates]
        for future in as_completed(futures):
            try:
                events.extend(future.result())
            except Exception:
                continue
    return events


def load_manual_catalysts(start: date, days: int = 14) -> list[dict]:
    path = ROOT / "data" / "manual" / "catalysts.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    end = start + timedelta(days=days)
    result = []
    for event in payload.get("events", []):
        try:
            event_date = date.fromisoformat(event["date"])
        except (KeyError, ValueError):
            continue
        if start <= event_date <= end and event.get("source_url"):
            result.append(event)
    return result


def fetch_fred_macro_calendar(start: date, days: int = 14) -> list[dict]:
    """Read future key U.S. macro release dates from FRED's public calendar."""
    end = start + timedelta(days=days)
    releases = {
        10: ("CPI", "Consumer Price Index", 5, "https://www.bls.gov/cpi/"),
        46: ("PPI", "Producer Price Index", 4, "https://www.bls.gov/ppi/"),
        50: ("NFP", "Employment Situation / Nonfarm Payrolls", 5, "https://www.bls.gov/ces/"),
        101: ("Fed", "FOMC Press Release", 5, "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
    }

    def fetch_release(rid: int, year: int) -> list[dict]:
        kind, title, importance, official_url = releases[rid]
        url = f"https://fred.stlouisfed.org/releases/calendar?rid={rid}&y={year}"
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=12) as response:
                html = response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError):
            return []
        found = []
        for raw in re.findall(r'font-weight:\s*bold;">\s*([^<]+?)\s*</span>', html):
            clean = " ".join(raw.split())
            try:
                event_date = datetime.strptime(clean, "%A %B %d, %Y").date()
            except ValueError:
                continue
            if start <= event_date <= end:
                found.append({
                    "date": event_date.isoformat(),
                    "time": "See official release calendar",
                    "type": kind,
                    "title": title,
                    "importance": importance,
                    "status": "official_calendar",
                    "source_id": "fred",
                    "source_url": official_url,
                    "note": "Date distributed by FRED from the underlying official agency; verify time at the direct official source.",
                })
        return found

    years = sorted({start.year, end.year})
    events = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_release, rid, year) for rid in releases for year in years]
        for future in as_completed(futures):
            events.extend(future.result())
    unique = {(item["date"], item["title"]): item for item in events}
    return list(unique.values())


def load_previous_snapshots() -> list[dict]:
    snapshots = []
    for path in sorted((ROOT / "data" / "daily").glob("*.json")):
        try:
            snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return snapshots


def build_validation(previous: list[dict], sectors: list[dict], series: dict[str, dict]) -> dict:
    if not previous:
        return {"status": "pending", "message": "First tracked report; validation begins next trading session.", "latest": None, "history": []}
    prior = previous[-1]
    prediction = prior.get("research_prediction") or {}
    predicted_sector = prediction.get("sector")
    current_sector = next((item for item in sectors if item["name"] == predicted_sector), None)
    qqq_1d = series.get("QQQ", {}).get("returns", {}).get("1d")
    if not current_sector or current_sector.get("return_1d") is None or qqq_1d is None:
        return {"status": "pending", "message": "Required next-session comparison data is unavailable.", "latest": None, "history": prior.get("validation", {}).get("history", [])}
    excess = round(current_sector["return_1d"] - qqq_1d, 3)
    correct = excess > 0
    record = {
        "prediction_date": prior.get("metadata", {}).get("report_date"),
        "evaluation_date": datetime.now(ADELAIDE).date().isoformat(),
        "prediction": prediction.get("statement"),
        "sector": predicted_sector,
        "sector_return_1d": current_sector["return_1d"],
        "qqq_return_1d": qqq_1d,
        "excess_return": excess,
        "correct": correct,
        "rule": "Correct when the selected sector basket outperforms QQQ over the next observed session.",
    }
    history = list(prior.get("validation", {}).get("history", []))
    if not any(row.get("prediction_date") == record["prediction_date"] for row in history):
        history.append(record)
    completed = [row for row in history if isinstance(row.get("correct"), bool)]
    accuracy = round(sum(1 for row in completed if row["correct"]) / len(completed) * 100, 1) if completed else None
    return {"status": "available", "message": "Rule-based next-session validation; not a trading backtest.", "latest": record, "history": history[-365:], "accuracy": accuracy, "sample_size": len(completed)}


def risk_on_score(series: dict[str, dict], watchlist: list[dict]) -> tuple[int, list[dict]]:
    components = []

    def add(name: str, condition: bool | None, weight: int, detail: str) -> None:
        points = weight if condition is True else 0 if condition is False else None
        components.append({"name": name, "points": points, "weight": weight, "detail": detail})

    add("QQQ 20D trend", (series.get("QQQ", {}).get("returns", {}).get("20d") or 0) > 0, 20, "Positive 20-session return")
    add("SMH 20D trend", (series.get("SMH", {}).get("returns", {}).get("20d") or 0) > 0, 15, "Positive semiconductor trend")
    add("SOX 20D trend", (series.get("SOX", {}).get("returns", {}).get("20d") or 0) > 0, 15, "Positive semiconductor index trend")
    hyg = series.get("HYG", {}).get("returns", {}).get("20d")
    lqd = series.get("LQD", {}).get("returns", {}).get("20d")
    add("Credit risk appetite", hyg is not None and lqd is not None and hyg > lqd, 15, "HYG outperforms LQD over 20 sessions")
    vix5 = series.get("VIX", {}).get("returns", {}).get("5d")
    add("Volatility", vix5 is not None and vix5 < 0, 15, "VIX falling over 5 sessions")
    dgs10_5 = series.get("US10Y", {}).get("returns", {}).get("5d")
    add("Rate pressure", dgs10_5 is not None and dgs10_5 <= 1.5, 10, "10Y yield not surging over 5 sessions")
    covered = [row for row in watchlist if row.get("status") == "available"]
    breadth = sum(1 for row in covered if row.get("trend") == "上升") / len(covered) if covered else None
    add("Watchlist breadth", breadth is not None and breadth >= 0.5, 10, "At least half of tracked stocks are in an uptrend")
    total_weight = sum(item["weight"] for item in components if item["points"] is not None)
    earned = sum(item["points"] for item in components if item["points"] is not None)
    score = round(earned / total_weight * 100) if total_weight else 50
    return score, components


def classify_regime(score: int, series: dict[str, dict]) -> str:
    qqq5 = series.get("QQQ", {}).get("returns", {}).get("5d") or 0
    sox5 = series.get("SOX", {}).get("returns", {}).get("5d") or 0
    vix5 = series.get("VIX", {}).get("returns", {}).get("5d") or 0
    if score >= 75 and qqq5 > 0 and sox5 > 0:
        return "Risk On"
    if score <= 30 and vix5 > 0:
        return "Deleveraging"
    if qqq5 > 0 and sox5 < 0:
        return "Sector Rotation"
    if qqq5 > 0 and vix5 < -5 and score < 60:
        return "Short Covering"
    if score >= 55:
        return "Recovery"
    return "Risk Off"


def generate_report_html(data_url: str, title: str) -> str:
    return f"""<!doctype html>
<html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">
<meta name=\"theme-color\" content=\"#071018\"><title>{title}</title>
<link rel=\"stylesheet\" href=\"../assets/styles.css\"></head>
<body><div id=\"app\" class=\"loading\">正在載入 Institutional AI Infrastructure Research…</div>
<script>window.BRIEF_DATA_URL={json.dumps(data_url)};</script><script src=\"../assets/app.js\"></script></body></html>"""


def update_archive_index(report_date: str) -> None:
    report_files = sorted((ROOT / "reports").glob("20??-??-??.html"), reverse=True)
    rows = "\n".join(
        f'<a class="archive-row" href="reports/{path.name}"><span>{path.stem}</span><strong>開啟報告 →</strong></a>'
        for path in report_files
    )
    html = f"""<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta name=\"theme-color\" content=\"#071018\"><title>Institutional AI Infrastructure Daily Research</title><link rel=\"stylesheet\" href=\"assets/styles.css\"></head><body>
<main class=\"landing\"><div class=\"eyebrow\">CHARLES DATA · DAILY RESEARCH OS</div><h1>Institutional AI Infrastructure<br>Daily Research</h1><p>以市場結構、相對強弱、可驗證資金流與研究驗證為核心；不把新聞摘要包裝成投資研究。</p><a class=\"primary-link\" href=\"latest/\">開啟最新報告 · {report_date} →</a><section class=\"archive-card\"><h2>歷史報告</h2>{rows}</section></main></body></html>"""
    (ROOT / "index.html").write_text(html, encoding="utf-8")


def write_support_csvs(snapshot: dict, full_market: dict[str, dict]) -> None:
    market_path = ROOT / "data" / "price_history.csv"
    with market_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["ticker", "date", "close", "volume", "source"])
        for key, item in full_market.items():
            for row in item.get("history", []):
                writer.writerow([key, row["date"], row["close"], row.get("volume"), item["source_id"]])
    watch_path = ROOT / "data" / "watchlist.csv"
    with watch_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["ticker", "sector", "price", "data_date", "trend", "relative_strength_20d", "alpha_score", "risk_score", "pm_rating", "score_coverage"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(snapshot["watchlist"])
    sector_path = ROOT / "data" / "sector_rotation.csv"
    with sector_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["rank", "name", "return_1d", "return_5d", "return_20d", "relative_strength_20d", "breadth_above_20d", "score"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(snapshot["sectors"])


def build(report_date: date | None = None) -> dict:
    now = datetime.now(ADELAIDE)
    report_date = report_date or now.date()
    all_symbols = dict(MARKET_SYMBOLS)
    for ticker in WATCHLIST:
        all_symbols[ticker] = ticker
    for members in SECTOR_BASKETS.values():
        for ticker in members:
            all_symbols[ticker] = ticker

    raw = {}
    errors = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(yahoo_history, symbol): key for key, symbol in all_symbols.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                raw[key] = future.result()
            except Exception as exc:  # data gaps must not halt the full report
                errors.append({"key": key, "error": str(exc)})

    series = {key: summarize_series(history, key) for key, history in raw.items()}

    # Replace Yahoo yield proxy with official FRED DGS10 when available.
    try:
        req = Request("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=25) as response:
            lines = response.read().decode("utf-8").splitlines()
        rows = []
        for row in csv.DictReader(lines):
            value = row.get("DGS10")
            if value and value != ".":
                rows.append({"date": row["observation_date"], "close": float(value), "volume": None})
        if len(rows) >= 61:
            history = {"symbol": "DGS10", "currency": "%", "rows": rows[-400:], "source_id": "fred", "fetched_at": datetime.now(timezone.utc).isoformat()}
            series["US10Y"] = summarize_series(history, "US10Y")
    except Exception as exc:
        errors.append({"key": "US10Y/FRED", "error": str(exc)})

    sectors = build_sector_rankings(series)
    watchlist = build_watchlist(series, sectors)
    score, score_components = risk_on_score(series, watchlist)
    regime = classify_regime(score, series)
    flows = {etf: load_flow_csv(etf) for etf in ("SMH", "SOXX")}
    flow_available = any(item["status"] == "available" for item in flows.values())
    top_sector = sectors[0] if sectors else None
    previous = [
        item for item in load_previous_snapshots()
        if item.get("metadata", {}).get("report_date") != report_date.isoformat()
    ]
    prior_day = previous[-1] if previous else None
    prior_week = previous[-5] if len(previous) >= 5 else None
    prior_day_ranks = {row["name"]: row.get("rank") for row in (prior_day or {}).get("sectors", [])}
    prior_week_ranks = {row["name"]: row.get("rank") for row in (prior_week or {}).get("sectors", [])}
    for sector in sectors:
        old_day = prior_day_ranks.get(sector["name"])
        old_week = prior_week_ranks.get(sector["name"])
        sector["rank_yesterday"] = old_day
        sector["rank_change_1d"] = old_day - sector["rank"] if old_day is not None else None
        sector["rank_last_week"] = old_week
        sector["rank_change_1w"] = old_week - sector["rank"] if old_week is not None else None
    alpha_timeline = []
    for old in previous[-364:]:
        old_sectors = old.get("sectors") or []
        if not old_sectors:
            continue
        leader = min(old_sectors, key=lambda item: item.get("rank", 999))
        alpha_timeline.append({
            "date": old.get("metadata", {}).get("report_date"),
            "leader": leader.get("name"),
            "leader_score": leader.get("score"),
            "smh_flow_status": old.get("flows", {}).get("SMH", {}).get("status", "pending"),
            "smh_flow_5d": old.get("flows", {}).get("SMH", {}).get("windows", {}).get("5d"),
        })
    if top_sector:
        alpha_timeline.append({"date": report_date.isoformat(), "leader": top_sector["name"], "leader_score": top_sector["score"], "smh_flow_status": flows["SMH"]["status"], "smh_flow_5d": flows["SMH"]["windows"].get("5d")})
    validation = build_validation(previous, sectors, series)
    catalysts = load_manual_catalysts(report_date) + fetch_fred_macro_calendar(report_date) + fetch_nasdaq_earnings(report_date)
    catalysts.sort(key=lambda item: (item["date"], -int(item.get("importance", 1))))

    if top_sector and top_sector["score"] >= 65:
        alpha = f"{top_sector['name']} retains the strongest price/relative-strength setup, but conviction is conditional until validated institutional flow confirms it."
        prediction = {"sector": top_sector["name"], "statement": f"{top_sector['name']} is expected to outperform QQQ over the next observed session.", "confidence": "conditional"}
    else:
        alpha = "No high-conviction alpha: leadership is not strong enough to justify a directional sector tilt."
        prediction = {"sector": top_sector["name"] if top_sector else None, "statement": "No high-conviction directional call.", "confidence": "low"}

    qqq20 = series.get("QQQ", {}).get("returns", {}).get("20d")
    sox20 = series.get("SOX", {}).get("returns", {}).get("20d")
    narrative = "AI Infrastructure"
    if series.get("US10Y", {}).get("returns", {}).get("5d", 0) > 2:
        narrative = "Higher Rates"
    elif qqq20 is not None and sox20 is not None and sox20 < qqq20:
        narrative = "AI ROI / Semiconductor Rotation"

    pm_actions = []
    for sector in sectors:
        rating = "Overweight" if sector["score"] >= 70 else "Underweight" if sector["score"] <= 35 else "Neutral"
        pm_actions.append({
            "segment": sector["name"],
            "rating": rating,
            "reason": f"20D relative strength {sector['relative_strength_20d']:+.1f} pts vs QQQ; breadth {sector['breadth_above_20d'] if sector['breadth_above_20d'] is not None else 'Pending'}%.",
            "approval_status": "Analytical posture — not an approved mandate rule",
        })

    divergences = []
    if flows["SMH"]["status"] == "available":
        price5 = series.get("SMH", {}).get("returns", {}).get("5d")
        flow5 = flows["SMH"]["windows"].get("5d")
        if price5 is not None and flow5 is not None and ((price5 > 0 > flow5) or (price5 < 0 < flow5)):
            divergences.append({"asset": "SMH", "price_5d": price5, "flow_5d": flow5, "signal": "Price ↑ / Flow ↓" if price5 > 0 else "Price ↓ / Flow ↑", "strength": "High"})
    else:
        divergences.append({"asset": "SMH", "signal": "Pending", "reason": "Validated ETF flow unavailable; no divergence is inferred from price/volume."})

    data_dates = [item["data_date"] for item in series.values() if item.get("data_date")]
    # Daily archives retain the full SMH chart history and compact summaries for
    # all other series. The de-duplicated cross-sectional history lives in CSV.
    compact_market = {}
    for key, item in series.items():
        compact_market[key] = dict(item)
        if key != "SMH":
            compact_market[key]["history"] = []

    snapshot = {
        "schema_version": "1.0.0",
        "metadata": {
            "report_date": report_date.isoformat(),
            "generated_at": now.isoformat(),
            "generated_timezone": "Australia/Adelaide",
            "market_data_as_of": max(data_dates) if data_dates else None,
            "coverage": {"requested_series": len(all_symbols) + 1, "available_series": len(series), "errors": len(errors)},
            "methodology": "Rules-based daily monitoring; factual data and interpretation are separately labelled.",
        },
        "executive": {
            "regime": regime,
            "narrative": narrative,
            "biggest_alpha": alpha,
            "risk_on_score": score,
            "score_components": score_components,
            "pm_conclusion": "Maintain exposure only where relative strength and breadth agree. Do not call institutional accumulation until validated ETF or other institutional flow data is available; use position sizing and next-session confirmation instead of chasing headlines.",
            "daily_questions": [
                {"question": "What is the market pricing today?", "answer": f"{regime}; dominant cross-asset narrative: {narrative}."},
                {"question": "Where is institutional money flowing?", "answer": "Confirmed ETF flow is available." if flow_available else "Not decision-grade: validated institutional flow data is unavailable."},
                {"question": "Which indicators are leading?", "answer": f"Relative strength and breadth currently rank {top_sector['name'] if top_sector else 'no segment'} first."},
                {"question": "Which news is lagging?", "answer": "Headlines unsupported by a change in price trend, relative strength, breadth or a confirmed catalyst are treated as lagging information."},
                {"question": "What is today’s highest-conviction Alpha?", "answer": alpha},
                {"question": "How would an AI Infrastructure PM adjust?", "answer": "Tilt only toward confirmed leaders, keep non-leaders neutral/underweight, and wait for validated flow before increasing gross exposure."},
            ],
        },
        "market": compact_market,
        "flows": flows,
        "sectors": sectors,
        "watchlist": watchlist,
        "divergences": divergences,
        "institutional_money_flow": {
            "etf_flow": "available" if flow_available else "pending",
            "dark_pool": "data_unavailable",
            "block_trades": "data_unavailable",
            "options_flow": "data_unavailable",
            "prime_broker_positioning": "data_unavailable",
            "note": "Unavailable datasets are not proxied with price or volume.",
        },
        "catalysts": catalysts,
        "pm_actions": pm_actions,
        "validation": validation,
        "alpha_timeline": alpha_timeline[-365:],
        "research_prediction": prediction,
        "errors": errors,
        "sources": SOURCE_CATALOG,
    }

    daily_path = ROOT / "data" / "daily" / f"{report_date.isoformat()}.json"
    if daily_path.exists() and not os.environ.get("ALLOW_SAME_DAY_REBUILD"):
        existing = json.loads(daily_path.read_text(encoding="utf-8"))
        if existing.get("metadata", {}).get("report_date") == report_date.isoformat():
            raise FileExistsError(f"Historical snapshot already exists: {daily_path}. Set ALLOW_SAME_DAY_REBUILD=1 for an explicit same-day refresh.")
    daily_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "data" / "latest.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "latest" / "index.html").write_text(generate_report_html("../data/latest.json", "Latest Institutional AI Infrastructure Daily Research"), encoding="utf-8")
    report_path = ROOT / "reports" / f"{report_date.isoformat()}.html"
    if not report_path.exists():
        report_path.write_text(generate_report_html(f"../data/daily/{report_date.isoformat()}.json", f"{report_date.isoformat()} Institutional AI Infrastructure Daily Research"), encoding="utf-8")
    update_archive_index(report_date.isoformat())
    write_support_csvs(snapshot, series)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Report date in YYYY-MM-DD (defaults to Adelaide today)")
    parser.add_argument("--check-adelaide-hour", action="store_true", help="Exit 0 only at 21:00 Australia/Adelaide; exit 3 otherwise")
    args = parser.parse_args()
    if args.check_adelaide_hour:
        return 0 if datetime.now(ADELAIDE).hour == 21 else 3
    report_date = date.fromisoformat(args.date) if args.date else None
    snapshot = build(report_date)
    print(json.dumps({
        "status": "ok",
        "report_date": snapshot["metadata"]["report_date"],
        "market_data_as_of": snapshot["metadata"]["market_data_as_of"],
        "risk_on_score": snapshot["executive"]["risk_on_score"],
        "regime": snapshot["executive"]["regime"],
        "available_series": snapshot["metadata"]["coverage"]["available_series"],
        "errors": len(snapshot["errors"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
