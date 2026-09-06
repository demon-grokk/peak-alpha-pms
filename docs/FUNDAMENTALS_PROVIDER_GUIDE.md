# Fundamentals Data Provider Guide & Architecture

## Overview
Peak Alpha PMS uses a modular fundamental ingestion design. The Filter 2 engine (`src/filters/ath_profit.py`) evaluates trailing 12-month (TTM) profit against SQLite `fundamentals_quarterly`, completely decoupled from specific data providers.

---

## 1. Active Provider: ScanX / Dhan API (`src/ingestion/scanx_fundamentals.py`)

### Features
- **Coverage**: Continuous 46+ historical quarters for Indian equities.
- **Batching**: Multi-ISIN request payload (`{"data": {"isins": [...]}}`), enabling **1 single HTTP POST call per screening run**.
- **Full Valuation & CV Metrics Harvested**:
  - **Company Valuation (CV)**: `bse_code`, `market_cap_category` (Largecap/Midcap/Smallcap), `sub_sector`, `dividend_yield`, `face_value`, `market_cap`, `pe_ratio`, `pb_ratio`, `book_value`.
  - **Income Statement**: Net Profit, Revenue, EBITDA, Expenses, Depreciation, Interest, Tax.
  - **Quality (ROCE / ROE)**: Return on Capital Employed (%), Return on Equity (%).
  - **Shareholding Pattern**: Promoter (%), FII (%), DII (%).

### Authentication & Environment Variable Configuration
- Token location: Loaded dynamically from **`.env`** (`SCANX_AUTH_TOKEN`).
- `.env.example` template provided in repo root for clean setup.
- **Expiry Behavior**:
  - If the API returns HTTP 401/403 or auth failure, the ingestion layer logs a `[CRITICAL WARNING]` alert:
    `"[CRITICAL WARNING] ScanX API Authentication Token expired or unauthorized! Please update SCANX_AUTH_TOKEN in .env file."`
  - Affected stocks are safely tagged `needs_manual_review = True` with reason `"ScanX API Token Expired (401/403)"`. Execution continues smoothly without crashing.

---

## 2. Fallback / Alternative Provider Strategy

If the ScanX API or auth token is unavailable, the pipeline supports seamless provider switching:

### Provider A: Yahoo Finance Ingestion (`src/ingestion/fundamentals.py`)
- Pulls quarterly financials via `yfinance`.
- Continuity check flags missing quarter gaps (e.g. omitted Q2 FY26) to `needs_manual_review = True`.

### Provider B: Screener.in Manual Review Export (`src/core/manual_review.py`)
- Any stock routed to `needs_manual_review = True` is exported to `data/exports/screener_manual_review_watchlist.csv`.
- Human review results are logged back into SQLite via `ManualReviewManager.record_review_outcome()`.

---

## 3. SQLite Database Storage

Fundamental & CV metrics are stored across two tables:
1. `fundamentals_quarterly`: Historical quarterly PAT, Revenue, EBITDA.
2. `stocks`: Extended with `bse_code`, `market_cap_category`, `sub_sector`, `dividend_yield`, `face_value`, `market_cap`, `pe_ratio`, `pb_ratio`, `book_value`, `roce`, `roe`, `promoter_holding`, `fii_holding`, `dii_holding`.
