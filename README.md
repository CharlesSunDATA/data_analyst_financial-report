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

## ETF flow schema

`date,net_flow_usd,source,source_url,updated_at`

The pipeline accepts source-backed observations only. It does not estimate ETF flow from price or volume.

## Research validation

The current validation rule measures whether the prior report's top-ranked segment outperformed QQQ over the next observed session. It is labelled as research tracking, not as a trading backtest or win rate.
