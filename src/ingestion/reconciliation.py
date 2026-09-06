"""
Periodic Full-History Price Reconciliation Module for Peak Alpha PMS.
Performs full-history (period="max") re-fetches per symbol, diffs the complete price series against stored SQLite data,
identifies scale shifts and unadjusted anomalies, updates clean uniform adjustments, and flags non-uniform revisions for quarantine.
"""
import datetime
import yfinance as yf
import pandas as pd
from typing import List, Optional, Dict, Tuple, Set, Any
from config.constants import CORPORATE_ACTION_REL_TOL, UNIFORMITY_REL_TOL, PRICE_BATCH_SIZE
from src.db.database import db_manager
from src.utils.helpers import format_ticker_nse
from src.utils.logger import logger
from src.ingestion.price_fetcher import _process_and_upsert_yf_df, log_corporate_action_event

def reconcile_full_price_history(
    symbols: Optional[List[str]] = None,
    batch_size: int = PRICE_BATCH_SIZE
) -> Dict[str, Any]:
    """
    Executes a periodic full-history reconciliation across target symbols.
    Diffs full yfinance price series against stored SQLite price history.
    """
    logger.info("=== Starting Periodic Full-History Price Reconciliation ===")

    if not symbols:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol FROM stocks WHERE symbol NOT LIKE 'DUMMY%';")
            symbols = [r["symbol"] for r in cursor.fetchall()]

    symbols = [s for s in symbols if not s.startswith("DUMMY")]
    logger.info(f"Reconciling full historical price series for {len(symbols)} universe stocks...")

    clean_count = 0
    refetched_count = 0
    flagged_anomaly_count = 0
    flagged_symbols = []

    total_batches = (len(symbols) + batch_size - 1) // batch_size
    upsert_sql = """
    INSERT INTO price_history (symbol, date, open, high, low, close, adj_close, volume)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(symbol, date) DO UPDATE SET
        open = excluded.open,
        high = excluded.high,
        low = excluded.low,
        close = excluded.close,
        adj_close = excluded.adj_close,
        volume = excluded.volume;
    """

    for b_idx, i in enumerate(range(0, len(symbols), batch_size), start=1):
        batch = symbols[i : i + batch_size]
        formatted_map = {format_ticker_nse(s): s for s in batch}
        formatted_tickers = list(formatted_map.keys())

        logger.info(f"[PROGRESS] [Reconciliation] Fetching full historical series batch {b_idx}/{total_batches} ({len(batch)} stocks: {batch[0]}..{batch[-1]})...")

        try:
            df = yf.download(
                formatted_tickers,
                period="max",
                auto_adjust=False,
                group_by="ticker",
                progress=False,
                threads=True
            )

            if df.empty:
                logger.warning(f"Batch {b_idx} returned empty dataframe from yfinance.")
                continue

            for yf_ticker, raw_symbol in formatted_map.items():
                if len(batch) == 1:
                    ticker_df = df
                    if isinstance(ticker_df.columns, pd.MultiIndex):
                        ticker_df.columns = ticker_df.columns.get_level_values(0)
                else:
                    if yf_ticker not in df.columns.levels[0]:
                        continue
                    ticker_df = df[yf_ticker]

                if "Adj Close" not in ticker_df.columns:
                    continue

                fresh_adj = ticker_df["Adj Close"].dropna()
                if fresh_adj.empty:
                    continue

                # Query stored price history for this symbol
                with db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT date, adj_close
                        FROM price_history
                        WHERE symbol = ? AND adj_close IS NOT NULL AND adj_close > 0;
                    """, (raw_symbol,))
                    rows = cursor.fetchall()

                if not rows:
                    # No stored data: insert fresh full backfill
                    _process_and_upsert_yf_df(ticker_df, [raw_symbol], {yf_ticker: raw_symbol}, upsert_sql)
                    refetched_count += 1
                    continue

                stored_map = {r["date"]: float(r["adj_close"]) for r in rows}
                ratios = []

                for dt_idx, fresh_val in fresh_adj.items():
                    date_str = dt_idx.strftime("%Y-%m-%d")
                    if date_str in stored_map:
                        stored_val = stored_map[date_str]
                        if fresh_val > 0 and stored_val > 0:
                            ratios.append(fresh_val / stored_val)

                if not ratios:
                    clean_count += 1
                    continue

                max_dev = max(abs(r - 1.0) for r in ratios)
                if max_dev <= CORPORATE_ACTION_REL_TOL:
                    clean_count += 1
                    continue

                # Scale shift / deviation detected across historical series
                base_ratio = ratios[0]
                is_uniform = all(
                    abs(r - base_ratio) / abs(base_ratio) <= UNIFORMITY_REL_TOL
                    for r in ratios
                )

                if is_uniform:
                    logger.info(f"[{raw_symbol}] Uniform historical scale shift reconciled (~{base_ratio:.4f}x). Re-writing full price series.")
                    with db_manager.get_connection() as conn:
                        conn.cursor().execute("DELETE FROM price_history WHERE symbol = ?;", (raw_symbol,))
                    _process_and_upsert_yf_df(ticker_df, [raw_symbol], {yf_ticker: raw_symbol}, upsert_sql)
                    log_corporate_action_event(raw_symbol, len(ratios), base_ratio, "REFETCH_TRIGGERED")
                    refetched_count += 1
                else:
                    # Check if discrepancy is driven by a large step drop (>25% drop) indicative of a structural demerger
                    min_r = min(ratios)
                    classification = "STRUCTURAL_DEMERGER_EVENT" if min_r < 0.75 else "UNRESOLVED_ANOMALY"

                    logger.warning(f"[{raw_symbol}] Non-uniform revision / structural break detected ({classification})! Ratio range: {min(ratios):.4f}x to {max(ratios):.4f}x across {len(ratios)} dates.")
                    log_corporate_action_event(raw_symbol, len(ratios), base_ratio, classification)
                    flagged_anomaly_count += 1
                    flagged_symbols.append(raw_symbol)

        except Exception as e:
            logger.error(f"Error during reconciliation batch {b_idx}: {e}")

    logger.info("=== Full-History Reconciliation Summary ===")
    logger.info(f"  - Total Evaluated: {len(symbols)}")
    logger.info(f"  - Verified Clean / Unchanged: {clean_count}")
    logger.info(f"  - Reconciled & Re-written (Uniform): {refetched_count}")
    logger.info(f"  - Flagged Anomaly / Quarantined: {flagged_anomaly_count} = {flagged_symbols}")

    return {
        "total_evaluated": len(symbols),
        "clean_count": clean_count,
        "refetched_count": refetched_count,
        "flagged_anomaly_count": flagged_anomaly_count,
        "flagged_symbols": flagged_symbols
    }

if __name__ == "__main__":
    reconcile_full_price_history(symbols=["RELIANCE", "TCS"])
