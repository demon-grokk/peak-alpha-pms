# Peak Alpha PMS — Task 1 User Manual & Access Guide

Welcome to **Peak Alpha PMS**! This manual explains how the project structure and SQLite database were created, how to interact with the database, and how to run tests and data pipelines on your machine.

---

## 1. Project Overview & Environment

All code is written in Python (using a local virtual environment `venv`) and backed by an SQLite database (`data/pms.db`).

### Directory Structure
```
peak-alpha-pms/
├── config/
│   ├── __init__.py
│   └── settings.py          # Centralized configuration (tickers, URLs, DB paths, thresholds)
├── data/
│   ├── pms.db               # SQLite database store
│   └── exports/             # Export directory for screening CSVs
├── src/
│   ├── db/
│   │   ├── database.py      # SQLite connection context manager
│   │   └── schema.py        # DDL table creation scripts
│   ├── execution/
│   │   └── allocation_engine.py # Layer 3 position allocation & 2-tier stop loss engine
│   ├── ingestion/
│   │   ├── universe.py      # NSE Nifty Total Market CSV downloader & parser
│   │   └── price_fetcher.py # yfinance price history fetcher (Stocks + BSE 500 benchmark)
│   ├── runners/
│   │   └── allocation_runner.py # Layer 3 evaluation runner & CLI orchestrator
│   └── utils/
│       ├── logger.py        # Structured console logging
│       └── helpers.py       # Ticker formatting, float epsilon math, return formulas
├── tests/
│   ├── test_ingestion.py    # Pytest unit tests for ingestion & database
│   └── test_allocation_engine.py # Pytest suite for Layer 3 allocation engine
├── venv/                    # Python virtual environment
├── high-alpha-pms-requirement.md
├── requirements.txt
└── USER_MANUAL.md           # This user manual
```

---

## 2. How the Database & Schema Were Created

The SQLite database file is located at `data/pms.db`.

### Tables Created (`src/db/schema.py`)
1. **`stocks`**: Master list of universe stocks (~752 stocks from Nifty Total Market).
   - Columns: `symbol`, `isin`, `name`, `sector`, `sector_index_mapping`, `listing_date`, `ath_baseline_start_date`, `is_financial_sector`, `strategy_tags`, `updated_at`
2. **`price_history`**: Daily adjusted OHLCV price history per stock.
   - Columns: `symbol`, `date`, `open`, `high`, `low`, `close`, `adj_close`, `volume` (Composite PK: `symbol`, `date`)
3. **`benchmark_history`**: Daily OHLCV price history for BSE 500 benchmark index (`BSE-500.BO`).
   - Columns: `benchmark_name`, `date`, `open`, `high`, `low`, `close`, `adj_close`, `volume`
4. **`fundamentals_quarterly`**: Quarterly PAT and revenue financials.
5. **`fundamentals_ttm`**: Computed TTM PAT ATH status and review flags.
6. **`screen_results`**: Screening results table storing per-run evaluations (`is_ath_price`, `is_emerging_candidate`, `near_ath_price_pct`, `is_ath_profit`, `outperforms_bse500`, `outperforms_sector`, `passes_all_filters`).
7. **`entry_evaluations`**: Point-in-time snapshot audit store for Layer 3 allocation decisions (`symbol`, `selection_reason`, `entry_price`, `sl_cand`, `r_pct`, `allocated_shares`, `allocated_capital`, `sizing_constraint_used`, `gate_decision`).
8. **`governance_flags`**: Governance review placeholders.
9. **`portfolio_positions`**: Target portfolio position tracking placeholders.


---

## 3. How to Access and Query the Database

You can access `data/pms.db` using Python or terminal SQLite CLI.

### Option A: Using Python (`./venv/bin/python`)

You can run interactive Python commands or create custom scripts:

```python
from src.db.database import db_manager

with db_manager.get_connection() as conn:
    cursor = conn.cursor()
    
    # 1. View total stock count
    cursor.execute("SELECT COUNT(*) FROM stocks;")
    print("Total Stocks:", cursor.fetchone()[0])
    
    # 2. View sample stock details
    cursor.execute("SELECT symbol, name, sector, isin, is_financial_sector FROM stocks LIMIT 5;")
    for row in cursor.fetchall():
        print(dict(row))
        
    # 3. View price history count
    cursor.execute("SELECT symbol, COUNT(*), MIN(date), MAX(date), MAX(adj_close) FROM price_history GROUP BY symbol;")
    for row in cursor.fetchall():
        print(dict(row))
```

### Option B: Using the query helper script (`query_db.py`)

No need to install `sqlite3` on Linux! You can run SQL queries directly using Python pandas formatting:

```bash
./venv/bin/python query_db.py "SELECT symbol, name, sector FROM stocks LIMIT 10;"
```

Or view benchmark data:
```bash
./venv/bin/python query_db.py "SELECT * FROM benchmark_history ORDER BY date DESC LIMIT 5;"
```


Run from your shell:

```bash
sqlite3 data/pms.db
```

Useful SQLite CLI commands:
```sql
.tables                                  -- List all 8 tables
.schema stocks                           -- View DDL schema for stocks table
SELECT symbol, name, sector FROM stocks LIMIT 10;
SELECT * FROM benchmark_history ORDER BY date DESC LIMIT 5;
.quit                                    -- Exit sqlite3
```

---

## 4. How to Run Ingestion & Tests

All commands should use the project virtual environment (`./venv/bin/python` or `./venv/bin/pytest`).

### 1. Re-initialize Database Schema
If you ever want to verify or re-create schema tables:
```bash
./venv/bin/python -m src.db.schema
```

### 2. Download / Update Universe Stocks (Nifty Total Market ~752 stocks)
```bash
./venv/bin/python -m src.ingestion.universe
```

### 3. Fetch Benchmark Data (`BSE-500.BO`)
```bash
./venv/bin/python -m src.ingestion.price_fetcher
```

### 4. Fetch Stock Prices for Anchor Stocks (e.g. RELIANCE, TCS, HDFCBANK)
```bash
./venv/bin/python -c '
from src.ingestion.price_fetcher import fetch_and_store_stock_prices
fetch_and_store_stock_prices(symbols=["RELIANCE", "TCS", "HDFCBANK"], period="max")
'
```

### 5. Run Automated Unit Tests (`pytest`)
To run all tests and verify project health:
```bash
./venv/bin/pytest tests/
```

---

## 5. Verification Check Summary

- **Universe**: Live fetch downloaded 752 constituents with ISIN, Name, Industry, and `is_financial_sector` flags.
- **Benchmark**: Ingested `BSE-500.BO` index history (latest close ~36,814 - confirmed index magnitude).
- **Price History**: Ingested daily price history for test stocks (`RELIANCE`, `TCS`, `HDFCBANK`) with automatic `listing_date` proxy derivation.
- **Tests**: `pytest tests/` passes 100%.

### 6. Phase 1 Data Source Limitation (Yahoo Finance 1996 Floor)
- **Pre-1996 Equities**: Yahoo Finance (our free data vendor) has a native data coverage floor of **1996-01-01** for all NSE equities. For older companies (e.g. RELIANCE, HDFCBANK), historical ATH checks effectively measure All-Time Highs back to ~1996.
- **Post-1996 Equities**: Stocks listed after 1996 (e.g. TCS in 2002, Coal India in 2010) show complete data starting from their exact listing date.

---

## 7. Task 2 — Quantitative Filter Engines & Usage

Task 2 added modular quantitative screening filters in `src/filters/`.

### Modules Created
1. **`src/filters/base.py`**: Base filter interface `BaseFilter` and standardized `FilterResult` object:
   - `result.symbol`: Ticker symbol (e.g. `RELIANCE`)
   - `result.passed`: Boolean (`True` / `False`)
   - `result.metrics`: Dictionary of computed metrics (e.g. `current_adj_close`, `ath_adj_close`, `stock_52w_return`, `bse500_52w_return`)
   - `result.reason`: Human-readable pass/fail reason.
2. **`src/filters/ath_price.py` (Filter 1 — ATH Price Detector)**:
   - Evaluates: `current_adj_close >= (max_historical_adj_close - 1e-6)`
   - Physical ATH Fact (`is_ath_price = True`): Set to `True` for ANY stock whose current price is at or above its historical high (`near_ath_price_pct = 0.0`), regardless of listing age.
   - Primary List Candidate (`is_ath_price = True AND is_emerging_candidate = False`): Requires >= 756 trading days (3 years) of stored history. Core trusted list for `passes_all_filters`.
   - Emerging Watchlist Candidate (`is_ath_price = True AND is_emerging_candidate = True`): Flags stocks at ATH with 252 to 755 trading days (1 to 3 years) of history for momentum monitoring.
   - Excluded (< 252 trading days): Excluded from both candidate lists.
   - *Note on Pre-1996 Equities*: For stocks listed before 1996 (e.g. Reliance), ATH means highest price since Yahoo Finance coverage floor (~1996). For post-1996 listings (e.g. TCS in 2002), it reflects full history since listing date.
3. **`src/filters/relative_strength.py` (Filter 3 Stage A — Relative Outperformance)**:
   - Evaluates: `stock_52w_return > bse500_52w_return`
   - Computes simple 52-week return over 252 trading days for both stock and `BSE-500.BO` index.

---

## 8. How to Test & Evaluate Filters

You can test any stock against the filter engines using Python:

```python
from src.filters.ath_price import ATHPriceFilter
from src.filters.relative_strength import RelativeStrengthFilter

ath_filter = ATHPriceFilter()
rs_filter = RelativeStrengthFilter()

# Evaluate RELIANCE
res_ath = ath_filter.evaluate("RELIANCE")
print("Filter 1 (ATH Price):", res_ath)
print("Metrics:", res_ath.metrics)

res_rs = rs_filter.evaluate("RELIANCE")
print("Filter 3 Stage A (Relative Strength):", res_rs)
print("Metrics:", res_rs.metrics)
```

Run test via terminal snippet:
```bash
./venv/bin/python -c '
from src.filters.ath_price import ATHPriceFilter
from src.filters.relative_strength import RelativeStrengthFilter

for s in ["RELIANCE", "TCS", "HDFCBANK"]:
    print(ATHPriceFilter().evaluate(s))
    print(RelativeStrengthFilter().evaluate(s))
'
```

---

## 9. Task 3 — Quarterly Fundamentals, Filter 2 & Manual Review Funnel

Task 3 added quarterly fundamentals ingestion, Filter 2 (ATH TTM Profit Detector), and the Screener.in manual review export manager.

### Modules Created
1. **`src/ingestion/fundamentals.py`**:
   - Downloads quarterly income statements via `yfinance`.
   - Extracts reported PAT, exceptional/unusual items, total revenue, and EBITDA.
   - Computes `pat_normalized = reported_pat - exceptional_items`.
   - *Enforced Rule*: If `yfinance` returns missing or unparseable fundamentals data for a stock, it tags `needs_manual_review = True` in `fundamentals_ttm`. Missing data is never silently dropped!
2. **`src/filters/ath_profit.py` (Filter 2 — ATH TTM Profit)**:
   - Calculates trailing 12-month normalized PAT (`ttm_pat_normalized`).
   - Compares current TTM PAT against historical maximum TTM PAT.
   - *Two-Signal Heuristic*: Flags stocks where PAT growth is out of proportion to Revenue ATH discrepancy for human review.
3. **`src/core/manual_review.py` (Manual Review Manager)**:
   - Exports pending review candidates to a Screener.in watchlist CSV (`data/exports/screener_manual_review_watchlist.csv`).
   - Logs manual review outcomes and verified TTM PAT figures back into SQLite.

### How to Test Task 3

Run fundamentals ingestion & Filter 2 evaluation:
```bash
./venv/bin/python -c '
from src.ingestion.fundamentals import fetch_and_store_fundamentals
from src.filters.ath_profit import ATHProfitFilter
from src.core.manual_review import ManualReviewManager

# 1. Ingest fundamentals for test stocks
fetch_and_store_fundamentals(["RELIANCE", "TCS", "HDFCBANK"])

# 2. Evaluate Filter 2
filter2 = ATHProfitFilter()
for s in ["RELIANCE", "TCS", "HDFCBANK"]:
    print(filter2.evaluate(s))

# 3. Export pending manual reviews to CSV
ManualReviewManager.export_screener_watchlist()
'
```

---

## 10. ScanX / Dhan API Fundamentals Integration & Modular Architecture

### Overview
ScanX fundamentals provider (`src/ingestion/scanx_fundamentals.py`) supplies 46+ quarters of continuous quarterly income statements, balance sheet data, ROCE/ROE quality ratios, and shareholding patterns for Indian equities.

### Key Capabilities & Configuration
- **Token Configuration**: Configured in `config/settings.py` via `SCANX_AUTH_TOKEN`.
- **Token Expiry Protocol**: If the API returns HTTP 401/403 (unauthorized/expired), a `[CRITICAL WARNING]` log alert is logged and affected stocks are routed to `needs_manual_review = True` with reason `"ScanX API Token Expired (401/403)"`. Execution never crashes.
- **Batching & Efficiency**: Batches up to 50 ISINs per request, requiring **only 1 HTTP request per screening run**.
- **Metrics Harvested in SQLite**:
  - `fundamentals_quarterly`: Historical quarterly PAT, Revenue, EBITDA.
  - `stocks`: `market_cap`, `pe_ratio`, `pb_ratio`, `book_value`, `roce`, `roe`, `promoter_holding`, `fii_holding`, `dii_holding`.

### How to Run ScanX Fundamentals Ingestion
```bash
./venv/bin/python -m src.ingestion.scanx_fundamentals
```

For modular architecture & fallback documentation, see [docs/FUNDAMENTALS_PROVIDER_GUIDE.md](file:///home/rajeev/Data/Personal%20Project/peak-alpha-pms/docs/FUNDAMENTALS_PROVIDER_GUIDE.md).

---

## 11. Two-Clock Scheduling Architecture & CLI Usage

### Overview
Peak Alpha PMS uses a decoupled two-clock execution model:
1. **Calendar Clock (Price Pipeline)**: Run daily/weekly to fetch daily price history and evaluate Filter 1 (ATH Price) & Filter 3 Stage A (52w Relative Strength).
2. **Event Clock (Fundamentals Pipeline)**: Run quarterly (or as earnings are reported) to fetch ScanX quarterly PAT and evaluate Filter 2 (ATH TTM Profit).

### Available CLI Commands (`cli.py`)

#### 1. Initialize Database Schema
```bash
./venv/bin/python cli.py init-db
```

#### 2. Ingest Universe Metadata
```bash
./venv/bin/python cli.py ingest-universe
```

#### 3. Run Daily Calendar Price Pipeline
```bash
./venv/bin/python cli.py run-price
# Options:
#   --no-fetch        Use existing stored price history without hitting yfinance
#   --symbols RELIANCE TCS   Run for specific symbols
```

#### 4. Run Event Fundamentals Pipeline
```bash
./venv/bin/python cli.py run-fundamentals
# Options:
#   --no-fetch        Use existing stored fundamentals without hitting ScanX API
```

#### 5. Run Full End-to-End Discovery Engine
```bash
./venv/bin/python cli.py run-all
```

#### 6. Export Manual Review Watchlist
```bash
./venv/bin/python cli.py export-reviews
```

#### 7. Evaluate Layer 3 Allocation & Stop-Loss Engine
```bash
# Evaluate screening candidates from latest run date
./venv/bin/python cli.py evaluate-allocation --capital 1000000 --cash 1000000

# Evaluate specific anchor stocks manually
./venv/bin/python cli.py evaluate-allocation --symbols RELIANCE TCS --capital 1000000 --cash 1000000
```

---

## 12. Layer 3 Allocation & Stop-Loss Engine & Audit Manual

### Core Parameters (`config/constants.py`)
- **`N_TARGET`**: 15 target portfolio stock slots.
- **`TOTAL_PORTFOLIO_RISK_PCT`**: 18.0% total portfolio risk budget.
- **`TRT_PCT`**: 1.2% Total Risk Target per trade ($0.012 \times \text{Capital\_C}$).
- **`MAX_ALLOC_PCT`**: 15.0% maximum allocation cap per trade ($0.15 \times \text{Capital\_C}$).
- **`MAX_RISK_GATE_PCT`**: 8.0% hard risk gate ceiling ($R_{\%} \le 8.0\%$).
- **`ALLOC_LOOKBACK_DAYS`**: 60 trading days lookback for ATR and ZigZag structural swing lows.

---

### Formulas & Derivations

1. **Volatility Floor ($S_{\text{atr}}$)**:
   $$S_{\text{atr}} = \text{Entry Price} - (2.0 \times \text{ATR}_{14, 60\text{d avg}})$$

2. **Structural Swing Low ($S_{\text{raw}}$)**:
   - Uses standard ZigZag state machine on 60-day price history.
   - Pivot Low confirmed when subsequent rally $\ge 1.0 \times \text{ATR}_{14, t}$.
   $$S_{\text{raw}} = \text{Pivot Low}_{\text{zigzag}} - (0.25 \times \text{ATR}_{14, \text{current}})$$

3. **Candidate Stop Loss ($\text{SL}_{\text{cand}}$)**:
   - Evaluates downside risk using a strict `min()` operator:
   $$\text{SL}_{\text{cand}} = \min(S_{\text{raw}}, S_{\text{atr}})$$
   - If no swing low is confirmed in 60 days, falls back to ATR floor (`used_fallback = 1`).

4. **Trade Downside Risk Percentage ($R_{\%}$)**:
   $$R_{\%} = \frac{\text{Entry Price} - \text{SL}_{\text{cand}}}{\text{Entry Price}} \times 100$$

5. **Risk-Based Sizing vs Allocation Cap**:
   $$\text{Raw Shares}_{\text{risk}} = \lfloor \frac{\text{Capital\_C} \times \text{TRT\_PCT}}{\text{Entry Price} - \text{SL}_{\text{cand}}} \rfloor$$
   $$\text{Raw Shares}_{\text{cap}} = \lfloor \frac{\text{Capital\_C} \times \text{MAX\_ALLOC\_PCT}}{\text{Entry Price}} \rfloor$$
   $$\text{Allocated Shares} = \min(\text{Raw Shares}_{\text{risk}}, \text{Raw Shares}_{\text{cap}})$$

6. **Hard Risk Gating Rules**:
   - `APPROVED`: $R_{\%} \le 8.0\%$ and required capital $\le \text{Available Cash}$.
   - `REJECTED_RISK_GT_8PCT`: $R_{\%} > 8.0\%$. (Allocated Shares = 0).
   - `INSUFFICIENT_CASH`: Required capital $> \text{Available Cash}$.

---

### Database Auditing SQL (`entry_evaluations`)

Query point-in-time allocation decisions from SQLite:
```python
from src.db.database import db_manager

with db_manager.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, selection_reason, entry_price, sl_cand, r_pct,
               allocated_shares, allocated_capital, sizing_constraint_used, gate_decision
        FROM entry_evaluations
        ORDER BY evaluation_ts DESC LIMIT 5;
    """)
    for row in cursor.fetchall():
        print(dict(row))
```

