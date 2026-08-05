# Institutional AI Infrastructure Daily Research System

A source-aware daily U.S. equity research pipeline focused on AI infrastructure, semiconductor leadership, market regime, relative strength, catalysts and research validation.

## Operating contract

- Runs at 21:00 `Australia/Adelaide` through GitHub Actions.
- Writes `latest/index.html` and archives `reports/YYYY-MM-DD.html` plus `data/daily/YYYY-MM-DD.json`.
- Never uses price or volume as a substitute for ETF flow, dark-pool, options-flow, block-trade or prime-broker data.
- Missing institutional data is displayed as `Pending` or `Data Unavailable`.
- Factual values carry source, data date and retrieval time. Interpretations are separately labelled.
- Same-day historical snapshots cannot be overwritten unless `ALLOW_SAME_DAY_REBUILD=1` is explicitly set.

## Local build

```bash
python3 scripts/build_daily_brief.py
python3 -m http.server 8080
```

Open `http://localhost:8080/latest/`.

## Data sources

- FRED CSV: U.S. 10-year Treasury yield.
- Yahoo Finance chart endpoint: market price and volume history.
- Nasdaq earnings calendar: event discovery; confirm material dates with company IR.
- `data/manual/catalysts.json`: only direct official-source events.
- `data/flows/*.csv`: validated ETF flow imports. Empty files are intentional until a reliable source is configured.
- `data/flows/confirmed_events.csv`: sparse source-backed flow events. These are displayed as evidence but never treated as a complete time series.

## ETF flow schema

`date,net_flow_usd,source,source_url,updated_at`

The pipeline accepts source-backed observations only. It does not estimate ETF flow from price or volume.

### Free continuous-history setup

ETF Global publishes a daily U.S. ETF fund-flow dataset through AWS Data Exchange. The listing is free, but an AWS account must subscribe and accept the provider terms. After downloading the CSV export, normalize it with:

```bash
python3 scripts/import_etf_global_flows.py --input /path/to/ETF_GLOBAL_FUND_FLOWS.csv --check
python3 scripts/import_etf_global_flows.py --input /path/to/ETF_GLOBAL_FUND_FLOWS.csv
ALLOW_SAME_DAY_REBUILD=1 python3 scripts/build_daily_brief.py
```

The importer accepts common ticker, date and fund-flow column aliases, filters SMH/SOXX, de-duplicates by date and writes the existing validated flow schema. It never fills missing dates with zero. ETF.com top-flow articles are retained separately as a sparse event tape and are excluded from Daily/5D/20D/60D/YTD calculations.

## Research validation

The current validation rule measures whether the prior report's top-ranked segment outperformed QQQ over the next observed session. It is labelled as research tracking, not as a trading backtest or win rate.
