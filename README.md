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

5. **Layer 3 Allocation & Stop-Loss Engine**
   - **2-Tier Stop Loss Floor**: Derives candidate stop loss via $\text{SL}_{\text{cand}} = \min(S_{\text{raw}}, S_{\text{atr}})$, where $S_{\text{atr}} = \text{Entry} - (2.0 \times \text{ATR}_{14, 60\text{d avg}})$ and $S_{\text{raw}} = \text{Pivot Low}_{\text{zigzag}} - (0.25 \times \text{ATR}_{14, \text{current}})$.
   - **Subsequent Bar Pivot Confirmation**: ZigZag state machine confirms pivot lows strictly on subsequent price action (rally $\ge 1.0 \times \text{ATR}_{14}$).
   - **Position Sizing & Risk Gating**: Allocates integer shares based on 1.2% Total Risk Target (`TRT_PCT`), capped at 15.0% maximum allocation (`MAX_ALLOC_PCT`). Hard risk gate rejects setups with trade risk $R_{\%} > 8.0\%$.
   - **Corporate Demerger & Anomaly Quarantine**: Filters out stocks undergoing structural breaks or unresolved price anomalies, and supports custom post-demerger ATH baselines via `ath_baseline_start_date`.
   - **Immutable Audit Persistence**: Stores point-in-time input snapshots, risk metrics, and position allocations in `entry_evaluations` table with audit indexing on `selection_reason` (`PASSES_ALL_FILTERS` vs `MANUAL_EXPLORATORY`).

---

## Cross-Platform & Zero-Config Database Architecture

Peak Alpha PMS is designed to run seamlessly across **macOS, Linux, and Windows** with zero external database dependencies:

- **Built-in SQLite Storage**: Backed by Python's standard `sqlite3` library. No need to install, configure, or run background database servers like PostgreSQL or MySQL.
- **Cross-Platform Path Handling**: File paths and directory structures are resolved dynamically using Python's `pathlib.Path`, eliminating OS-specific path separator issues (`\` vs `/`).
- **Self-Initializing Storage**: The database directory (`data/`), log directory (`data/logs/`), export directory (`data/exports/`), and SQLite schema (`data/pms.db`) are automatically provisioned on the first run of any CLI command or test.

---

## Installation & Setup

### Prerequisites
- **Python 3.10+** (macOS, Linux, or Windows)
- **Git**

### 1. Clone the Repository
```bash
git clone git@github.com:demon-grokk/peak-alpha-pms.git
cd peak-alpha-pms
```

### 2. Create and Activate Virtual Environment

- **macOS / Linux:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

- **Windows (Command Prompt):**
  ```cmd
  python -m venv venv
  venv\Scripts\activate.bat
  ```

- **Windows (PowerShell):**
  ```powershell
  python -m venv venv
  .\venv\Scripts\Activate.ps1
  ```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the example environment template:

- **macOS / Linux:**
  ```bash
  cp .env.example .env
  ```
- **Windows (Command Prompt / PowerShell):**
  ```cmd
  copy .env.example .env
  ```

Edit `.env` and set your ScanX authorization token:
```env
SCANX_AUTH_TOKEN="eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9..."
```

---

## How to Run End-to-End Scan & Allocation

Once your virtual environment is activated (`(venv)` appears in your prompt), run the CLI commands below:

### Step 1: Initialize Database & Ingest Universe (~752 Stocks)
Downloads official Nifty Total Market constituents from NSE and populates the SQLite database (the database file `data/pms.db` is auto-initialized):

```bash
python cli.py ingest-universe
```
*(Optional: You can also explicitly initialize or verify the schema anytime using `python cli.py init-db`)*

---

### Step 2: Run End-to-End Discovery Engine
Runs price fetching, Filters 1 & 3 Stage A, followed by ScanX fundamentals and Filter 2:

```bash
python cli.py run-all
```

---

### Step 3: Run Layer 3 Allocation & Stop-Loss Engine
Evaluates portfolio position sizing, 2-tier stop loss floor, and risk gating for passing candidates:

```bash
# Evaluate screening candidates from latest run date
python cli.py evaluate-allocation --capital 1000000 --cash 1000000

# Or evaluate specific anchor stocks manually (Manual Exploratory mode)
python cli.py evaluate-allocation --symbols RELIANCE TCS --capital 1000000 --cash 1000000
```

---

## Complete CLI Command Reference

All commands run portably across macOS, Linux, and Windows:

```bash
# Initialize or verify SQLite database schema
python cli.py init-db

# Ingest/update Nifty Total Market universe
python cli.py ingest-universe

# Run Calendar Price Pipeline only (Filters 1 & 3 Stage A)
python cli.py run-price

# Run Event Fundamentals Pipeline only (Filter 2)
python cli.py run-fundamentals

# Run full screener using cached data (Skip external network fetches)
python cli.py run-all --no-fetch

# Run screener for specific symbols
python cli.py run-all --symbols RELIANCE TCS HDFCBANK

# Reconcile full price history against Yahoo Finance to catch stock splits / adjustments
python cli.py reconcile-full-history

# Evaluate Layer 3 position allocation & stop-loss gating
python cli.py evaluate-allocation --capital 1000000 --cash 1000000

# Export pending manual reviews to Screener.in watchlist CSV
python cli.py export-reviews
```

*Tip for direct execution without activating venv:*
- **macOS / Linux**: `./venv/bin/python cli.py <command>`
- **Windows**: `.\venv\Scripts\python.exe cli.py <command>`

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

Run the full pytest suite to verify system and algorithmic integrity:

```bash
pytest tests/ -v
```

*(Or direct execution: `./venv/bin/pytest tests/ -v` on macOS/Linux, `.\venv\Scripts\pytest.exe tests/ -v` on Windows)*

*Test Suite Output*: All **35 unit and integration tests** pass cleanly.
