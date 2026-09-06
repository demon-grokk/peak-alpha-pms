"""
SQLite DDL Schema definitions for Peak Alpha PMS.
"""
from src.db.database import db_manager
from src.utils.logger import logger

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS stocks (
        symbol TEXT PRIMARY KEY,
        isin TEXT,
        name TEXT,
        sector TEXT,
        sub_sector TEXT,
        sector_index_mapping TEXT,
        bse_code TEXT,
        market_cap_category TEXT,
        listing_date DATE,
        ath_baseline_start_date DATE,
        is_financial_sector BOOLEAN DEFAULT 0,
        strategy_tags TEXT,
        market_cap REAL,
        pe_ratio REAL,
        pb_ratio REAL,
        book_value REAL,
        dividend_yield REAL,
        face_value REAL,
        roce REAL,
        roe REAL,
        promoter_holding REAL,
        fii_holding REAL,
        dii_holding REAL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS price_history (
        symbol TEXT,
        date DATE,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        adj_close REAL,
        volume INTEGER,
        PRIMARY KEY (symbol, date),
        FOREIGN KEY (symbol) REFERENCES stocks(symbol)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS benchmark_history (
        benchmark_name TEXT,
        date DATE,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        adj_close REAL,
        volume INTEGER,
        PRIMARY KEY (benchmark_name, date)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
        symbol TEXT,
        quarter_end DATE,
        reported_pat REAL,
        exceptional_items REAL,
        pat_normalized REAL,
        revenue REAL,
        ebitda REAL,
        last_result_date DATE,
        PRIMARY KEY (symbol, quarter_end),
        FOREIGN KEY (symbol) REFERENCES stocks(symbol)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fundamentals_ttm (
        symbol TEXT PRIMARY KEY,
        as_of_quarter DATE,
        ttm_pat_normalized REAL,
        is_ath_flag BOOLEAN DEFAULT 0,
        last_verified_ttm_pat DATE,
        needs_manual_review BOOLEAN DEFAULT 0,
        review_outcome TEXT,
        FOREIGN KEY (symbol) REFERENCES stocks(symbol)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS screen_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        run_date DATE,
        is_ath_price BOOLEAN DEFAULT 0,
        is_emerging_candidate BOOLEAN DEFAULT 0,
        near_ath_price_pct REAL DEFAULT NULL,
        is_ath_profit BOOLEAN DEFAULT 0,
        outperforms_bse500 BOOLEAN DEFAULT 0,
        outperforms_sector BOOLEAN DEFAULT NULL,
        passes_all_filters BOOLEAN DEFAULT 0,
        FOREIGN KEY (symbol) REFERENCES stocks(symbol)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS governance_flags (
        symbol TEXT PRIMARY KEY,
        promoter_holding_pct REAL,
        pledge_pct REAL,
        governance_flag BOOLEAN DEFAULT 0,
        notes TEXT,
        FOREIGN KEY (symbol) REFERENCES stocks(symbol)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_positions (
        symbol TEXT PRIMARY KEY,
        tier TEXT,
        allocation_pct REAL,
        entry_date DATE,
        entry_price REAL,
        current_alpha REAL,
        rank INTEGER,
        FOREIGN KEY (symbol) REFERENCES stocks(symbol)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS corporate_action_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        detection_date TEXT NOT NULL,
        overlap_start_date TEXT,
        overlap_end_date TEXT,
        overlap_count INTEGER,
        detected_ratio REAL,
        classification TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (symbol) REFERENCES stocks(symbol)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS entry_evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        evaluation_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        selection_reason TEXT NOT NULL CHECK(selection_reason IN ('PASSES_ALL_FILTERS', 'MANUAL_EXPLORATORY')),

        -- Input State (Point-in-time snapshot, immune to corporate action rescales)
        entry_price REAL NOT NULL,
        atr14_avg_60d REAL NOT NULL,
        atr14_current REAL NOT NULL,
        swing_low_price REAL,
        swing_low_date TEXT,
        used_fallback INTEGER DEFAULT 0 CHECK(used_fallback IN (0, 1)),

        -- Stop Loss Derivation
        s_raw REAL NOT NULL,
        s_atr REAL NOT NULL,
        sl_cand REAL NOT NULL,
        r_pct REAL NOT NULL,

        -- Portfolio Financial State at Execution Instant
        capital_c REAL NOT NULL,             -- Total Net Equity (Cash + Position Value)
        available_cash REAL NOT NULL,        -- Liquid Deployable Cash
        trt_pct REAL NOT NULL,               -- 0.012 (1.2% Risk Target)
        n_target INTEGER NOT NULL,           -- Target Portfolio Slot Count (15)
        max_alloc_pct REAL NOT NULL,         -- 0.15 (15% Allocation Cap)

        -- Sizing Calculations
        raw_shares_risk INTEGER NOT NULL,    -- floor((capital_c * trt_pct) / (entry_price - sl_cand))
        raw_shares_cap INTEGER NOT NULL,     -- floor((capital_c * max_alloc_pct) / entry_price)
        allocated_shares INTEGER NOT NULL,   -- min(raw_shares_risk, raw_shares_cap)
        allocated_capital REAL NOT NULL,     -- allocated_shares * entry_price
        allocation_pct REAL NOT NULL,        -- (allocated_capital / capital_c) * 100
        actual_risk_pct REAL NOT NULL,       -- (allocated_capital * (r_pct / 100)) / capital_c * 100
        sizing_constraint_used TEXT NOT NULL CHECK(sizing_constraint_used IN ('RISK_BASED', 'MAX_ALLOC_CAPPED')),

        -- Gate Outcomes
        gate_decision TEXT NOT NULL CHECK(gate_decision IN ('APPROVED', 'REJECTED_RISK_GT_8PCT', 'INSUFFICIENT_CASH')),
        
        -- Execution Tracking
        was_entered INTEGER DEFAULT 0 CHECK(was_entered IN (0, 1)),
        notes TEXT,

        FOREIGN KEY (symbol) REFERENCES stocks(symbol) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_entry_eval_sym_ts ON entry_evaluations(symbol, evaluation_ts);",
    "CREATE INDEX IF NOT EXISTS idx_entry_eval_decision ON entry_evaluations(gate_decision, was_entered);",
    "CREATE INDEX IF NOT EXISTS idx_entry_eval_reason ON entry_evaluations(selection_reason, evaluation_ts);"
]



def init_db():
    """Creates all schema tables in SQLite if they do not exist and applies migrations."""
    logger.info("Initializing database schema...")
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        for statement in DDL_STATEMENTS:
            cursor.execute(statement)

        # Migration: Ensure near_ath_price_pct column exists in screen_results
        cursor.execute("PRAGMA table_info(screen_results);")
        columns = [row["name"] if isinstance(row, dict) else row[1] for row in cursor.fetchall()]
        if "near_ath_price_pct" not in columns:
            logger.info("Migrating schema: Adding near_ath_price_pct column to screen_results table...")
            cursor.execute("ALTER TABLE screen_results ADD COLUMN near_ath_price_pct REAL DEFAULT NULL;")

        # Migration: Ensure ath_baseline_start_date column exists in stocks
        cursor.execute("PRAGMA table_info(stocks);")
        stock_cols = [row["name"] if isinstance(row, dict) else row[1] for row in cursor.fetchall()]
        if "ath_baseline_start_date" not in stock_cols:
            logger.info("Migrating schema: Adding ath_baseline_start_date column to stocks table...")
            cursor.execute("ALTER TABLE stocks ADD COLUMN ath_baseline_start_date DATE DEFAULT NULL;")

    logger.info("Database schema initialized successfully.")

if __name__ == "__main__":
    init_db()
