# Peak Alpha PMS — Quantitative Discovery Engine (v1.0)

**Peak Alpha PMS** is a local-first, zero-budget quantitative equity discovery engine for Indian stock markets. It implements multi-stage momentum and fundamental screening across the **Nifty Total Market Universe (~752 stocks)** using Python, SQLite, `yfinance`, and the **ScanX / Dhan Fundamental API**.

---

## Key Features

1. **Nifty Total Market Universe (~752 Stocks)**
   - Ingests official NSE Nifty Total Market constituents.
   - Automatically tags financial sector entities (Banks, NBFCs, Insurance) and maps BSE Security Codes and ISINs.

2. **Decoupled Two-Clock Architecture with Funnel Strategy**
   - **Calendar-Clock Price Pipeline**: Daily/weekly updates of price history (`yfinance`) and 52-week BSE 500 benchmark indices.
   - **Exact Market-Close Freshness Rules**: Evaluates fetch necessity based on exact IST trading hours (3:30 PM IST cutoff) and weekday schedules.
   - **Incremental Price Fetching**: Incrementally appends new trading days (`start = stored_max_date + 1 day`) to SQLite without full re-downloads.
   - **Strict Funnel Strategy**: Event-clock fundamental API fetches and Filter 2 (ATH TTM PAT) run **ONLY** for price-qualified candidates that pass Filter 1 & Filter 3 Stage A.

3. **Set-Based High Performance Querying**
   - Implements set-based SQL window queries (`MAX(adj_high)` and partitioning) to evaluate ATH status for the entire 752-stock universe in **~2.48 seconds**.

4. **Quantitative Filter Suite**
   - **Filter 1: Two-Tier True ATH Price Detector & Continuous `near_ath_price_pct` Metric**
     - *Primary List*: Stocks >= 756 trading days (3 calendar years) of listing history closing at True All-Time High price (evaluated against historical prior High wicks using `1e-6` floating point safety epsilon).
     - *Emerging Candidates Watchlist*: Stocks with 252-755 trading days (1 to 3 years) of history at ATH price.
     - *Continuous Distance to ATH (`near_ath_price_pct`)*: Daily-computed continuous metric stored on `screen_results` as a decimal (`0.0` for ATH, `0.05` for 5% below ATH). Evaluated for every stock with $\ge 252$ trading days history (stocks with $< 252$ days return `NULL`).
   - **Filter 2: ATH TTM Profit Detector**
     - Trailing 12-Month PAT ex-exceptional items. Uses raw PAT for financial sector entities.
     - Scoped 4-quarter window continuity check (>115 day gap in current window routes stock to manual review).
     - Two-signal heuristic fallback flags suspicious PAT vs Revenue growth discrepancies.
   - **Filter 3 Stage A: 52-Week Relative Outperformance**
     - Computes 52-week (252 trading day) percentage return for stock vs **BSE 500 benchmark** (`BSE-500.BO`).
     - Schema: `screen_results.outperforms_sector` is created as `BOOLEAN DEFAULT NULL` to preserve multi-stage screening semantics.

6. **Layer 3 Allocation & Stop-Loss Engine**
   - **2-Tier Stop Loss Floor**: Derives candidate stop loss via $\text{SL}_{\text{cand}} = \min(S_{\text{raw}}, S_{\text{atr}})$, where $S_{\text{atr}} = \text{Entry} - (2.0 \times \text{ATR}_{14, 60\text{d avg}})$ and $S_{\text{raw}} = \text{Pivot Low}_{\text{zigzag}} - (0.25 \times \text{ATR}_{14, \text{current}})$.
   - **Subsequent Bar Pivot Confirmation**: ZigZag state machine confirms pivot lows strictly on subsequent price action (rally $\ge 1.0 \times \text{ATR}_{14}$).
   - **Position Sizing & Risk Gating**: Allocates integer shares based on 1.2% Total Risk Target (`TRT_PCT`), capped at 15.0% maximum allocation (`MAX_ALLOC_PCT`). Hard risk gate rejects setups with trade risk $R_{\%} > 8.0\%$.
   - **Immutable Audit Persistence**: Stores point-in-time input snapshots, risk metrics, and position allocations in `entry_evaluations` table with audit indexing on `selection_reason` (`PASSES_ALL_FILTERS` vs `MANUAL_EXPLORATORY`).

---

## Quick-Start Setup

### 1. Configure Environment Variables
Create a `.env` file in the project root directory:

```bash
cp .env.example .env
```

Edit `.env` and insert your active ScanX authorization token:
```env
SCANX_AUTH_TOKEN="eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9..."
```

---

## How to Run End-to-End Scan & Allocation

Run the discovery & allocation engine in simple steps:

### Step 1: Ingest Universe Metadata (~752 Stocks)
Download the latest official Nifty Total Market constituents from NSE and populate the SQLite database:

```bash
./venv/bin/python cli.py ingest-universe
```

---

### Step 2: Run End-to-End Discovery Engine
Run the full price and fundamentals discovery pipelines across all 752 stocks:

```bash
./venv/bin/python cli.py run-all
```

---

### Step 3: Run Layer 3 Allocation & Stop-Loss Engine
Evaluate portfolio allocation and stop-loss derivation for screening candidates or explicit stocks:

```bash
# Evaluate screening candidates from latest run date
./venv/bin/python cli.py evaluate-allocation --capital 1000000 --cash 1000000

# Evaluate exploratory anchor stocks manually
./venv/bin/python cli.py evaluate-allocation --symbols RELIANCE TCS --capital 1000000 --cash 1000000
```

---

## Execution Command Options

```bash
# Run Calendar Price Pipeline only (Filters 1 & 3 Stage A)
./venv/bin/python cli.py run-price

# Run Event Fundamentals Pipeline only (Filter 2)
./venv/bin/python cli.py run-fundamentals

# Run full screener using cached data (Skip external network fetches)
./venv/bin/python cli.py run-all --no-fetch

# Run screener for specific symbols
./venv/bin/python cli.py run-all --symbols RELIANCE TCS HDFCBANK

# Evaluate Layer 3 position allocation & stop-loss gating
./venv/bin/python cli.py evaluate-allocation --capital 1000000 --cash 1000000

# Export pending manual reviews to Screener.in watchlist CSV
./venv/bin/python cli.py export-reviews
```

---

## Generated CSV Outputs (`data/exports/`)

| File Name | Content & Description |
| :--- | :--- |
| `primary_qualifying_stocks.csv` | **Primary Qualifying Candidates**: Stocks meeting all 3 filters with >= 3 years listing history. |
| `emerging_candidates.csv` | **Emerging Candidates**: Stocks with 1-3 years listing history at ATH price. |
| `screener_manual_review_watchlist.csv` | **Screener.in Watchlist**: Stocks routed to human review due to data quality gaps or heuristic flags. |

---

## System Logs

All screening steps, API status updates, allocation decisions, and progress breakdowns are logged to stdout and saved persistently to:
`data/logs/pms.log`

---

## Corporate Action Scale Shift Detector (2-Gate Architecture)

To handle stock splits, bonus issues, and retroactive price adjustments from Yahoo Finance without triggering unnecessary full-historical refetches:

1. **Gate 1 (Action Gate)**: Evaluates `max_dev = max(abs(r - 1.0) for r in ratios)`. If `max_dev <= CORPORATE_ACTION_REL_TOL` (`0.0008` / 0.08% noise floor), the stock short-circuits as `UNCHANGED` with 0 refetches.
2. **Gate 2 (Observability Gate)**: Executes only when Gate 1 flags `should_refetch = True`. It evaluates scale shift uniformity across the 20-day overlap window solely to set log severity (`INFO` with status `"REFETCH_TRIGGERED"` for uniform dividends/splits, `WARNING` with status `"REFETCH_TRIGGERED_NON_UNIFORM"` for non-uniform data revisions).

---

## Running Unit Tests

Run the complete test suite to verify system integrity:

```bash
./venv/bin/pytest tests/ -v
```

*Output*: All **32 unit tests** pass in ~2.9s.

