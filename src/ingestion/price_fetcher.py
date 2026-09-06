"""
Price data fetcher module for Peak Alpha PMS.
Downloads daily adjusted OHLCV data for universe stocks and BSE 500 benchmark via yfinance.
Implements exact market-close freshness checks, incremental price fetching, batch progress logging,
and retroactive corporate action scale-shift detection with automatic full refetches.
"""
import datetime
import yfinance as yf
import pandas as pd
from typing import List, Optional, Dict, Tuple, Set
from config.constants import (
    BSE_500_TICKER,
    CORPORATE_ACTION_OVERLAP_DAYS,
    CORPORATE_ACTION_REL_TOL,
    UNIFORMITY_REL_TOL,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    PRICE_BATCH_SIZE,
)
from src.db.database import db_manager
from src.utils.helpers import format_ticker_nse
from src.utils.logger import logger

def get_fetch_upper_bound(now: Optional[datetime.datetime] = None) -> datetime.date:
    """
    Returns the latest date that should be present in price_history, given current time.
    Weekends are skipped without computation. Holidays are NOT special-cased.
    """
    if now is None:
        now = datetime.datetime.now()

    today = now.date()

    if today.weekday() >= 5:  # Saturday=5, Sunday=6
        d = today
        while d.weekday() >= 5:
            d -= datetime.timedelta(days=1)
        return d

    market_closed_today = (now.hour, now.minute) >= (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)
    if market_closed_today:
        return today
    else:
        d = today - datetime.timedelta(days=1)
        while d.weekday() >= 5:
            d -= datetime.timedelta(days=1)
        return d

def get_stored_max_date(symbol: str) -> Optional[datetime.date]:
    """Returns maximum stored date for symbol in price_history, or None if no rows exist."""
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) as max_d FROM price_history WHERE symbol = ?;", (symbol,))
        row = cursor.fetchone()

    if not row or not row["max_d"]:
        return None

    try:
        return datetime.datetime.strptime(row["max_d"], "%Y-%m-%d").date()
    except ValueError:
        return None

def is_stock_price_cached(symbol: str, now: Optional[datetime.datetime] = None) -> bool:
    """
    Checks if stock price history in SQLite is current based on get_fetch_upper_bound.
    """
    stored_max = get_stored_max_date(symbol)
    if not stored_max:
        return False

    upper_bound = get_fetch_upper_bound(now)
    return stored_max >= upper_bound

def fetch_and_store_benchmark(period: str = "max", force_refetch: bool = False, now: Optional[datetime.datetime] = None) -> int:
    """Downloads BSE 500 benchmark index price history and stores in benchmark_history."""
    if not force_refetch and is_stock_price_cached("BSE500", now=now):
        logger.info("BSE 500 benchmark price history is fresh in SQLite. 0 downloads required.")
        return 0

    stored_max = get_stored_max_date("BSE500")
    if not force_refetch and stored_max:
        start_date = stored_max + datetime.timedelta(days=1)
        logger.info(f"Incrementally downloading benchmark index from {start_date}...")
        df = yf.download(BSE_500_TICKER, start=start_date.strftime("%Y-%m-%d"), auto_adjust=False, progress=False)
    else:
        logger.info(f"Downloading full benchmark index history for ticker: {BSE_500_TICKER} (period={period})...")
        df = yf.download(BSE_500_TICKER, period=period, auto_adjust=False, progress=False)

    if df.empty:
        logger.info(f"No new benchmark data returned for {BSE_500_TICKER}.")
        return 0

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    upsert_sql = """
    INSERT INTO benchmark_history (benchmark_name, date, open, high, low, close, adj_close, volume)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(benchmark_name, date) DO UPDATE SET
        open = excluded.open,
        high = excluded.high,
        low = excluded.low,
        close = excluded.close,
        adj_close = excluded.adj_close,
        volume = excluded.volume;
    """

    records = []
    for date_idx, row in df.iterrows():
        if pd.isna(row.get("Close")) and pd.isna(row.get("Adj Close")):
            continue
        date_str = date_idx.strftime("%Y-%m-%d")
        records.append((
            "BSE500",
            date_str,
            float(row["Open"]) if pd.notna(row.get("Open")) else None,
            float(row["High"]) if pd.notna(row.get("High")) else None,
            float(row["Low"]) if pd.notna(row.get("Low")) else None,
            float(row["Close"]) if pd.notna(row.get("Close")) else None,
            float(row["Adj Close"]) if pd.notna(row.get("Adj Close")) else (float(row["Close"]) if pd.notna(row.get("Close")) else None),
            int(row["Volume"]) if pd.notna(row.get("Volume")) else 0
        ))

    if records:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(upsert_sql, records)
        logger.info(f"Stored {len(records)} benchmark price records for BSE500.")

    return len(records)

def evaluate_overlap_window(
    symbol: str,
    ratios: List[float]
) -> Tuple[bool, str]:
    """
    Evaluates overlap ratios against strict 2-gate architecture:
    Gate 1: Action Gate (Decides should_refetch via CORPORATE_ACTION_REL_TOL = 0.0008)
    Gate 2: Observability Gate (Decides log severity and status: REFETCH_TRIGGERED vs REFETCH_TRIGGERED_NON_UNIFORM)

    Returns:
        (should_refetch: bool, status: str)
    """
    if not ratios:
        return False, "NO_OVERLAP_DATA"

    # GATE 1: ACTION GATE (The ONLY decider of refetch action)
    max_dev = max(abs(r - 1.0) for r in ratios)
    if max_dev <= CORPORATE_ACTION_REL_TOL:
        return False, "UNCHANGED"

    # GATE 2: OBSERVABILITY GATE (Runs ONLY when refetch is ALREADY locked in)
    base_ratio = ratios[0]
    is_uniform = all(
        abs(r - base_ratio) / abs(base_ratio) <= UNIFORMITY_REL_TOL
        for r in ratios
    )

    if is_uniform:
        logger.info(
            f"[{symbol}] Corporate action scale shift detected (Uniform ~{base_ratio:.4f}x). "
            f"Triggering full historical backfill."
        )
        status = "REFETCH_TRIGGERED"
    else:
        logger.warning(
            f"[{symbol}] Non-uniform data revision detected "
            f"(Range: {min(ratios):.4f}x to {max(ratios):.4f}x). "
            f"Triggering full historical backfill for safety."
        )
        status = "REFETCH_TRIGGERED_NON_UNIFORM"

    return True, status

def detect_corporate_action_mismatch(
    symbol: str,
    fresh_series: pd.Series
) -> Tuple[bool, Optional[str], Optional[float], int]:
    """
    Checks overlapping historical adj_close values for a symbol against freshly fetched yfinance data.
    Returns (mismatch_detected, status, detected_ratio, overlap_count).
    """
    sql = """
    SELECT date, adj_close
    FROM price_history
    WHERE symbol = ? AND adj_close IS NOT NULL AND adj_close > 0
    ORDER BY date DESC
    LIMIT ?;
    """
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (symbol, CORPORATE_ACTION_OVERLAP_DAYS))
        rows = cursor.fetchall()

    if not rows:
        return False, "NO_OVERLAP_DATA", None, 0

    stored_map = {row["date"]: float(row["adj_close"]) for row in rows}
    ratios = []

    for date_str, stored_val in stored_map.items():
        dt_idx = pd.to_datetime(date_str)
        if dt_idx in fresh_series.index:
            fresh_val = float(fresh_series.loc[dt_idx])
            if pd.isna(fresh_val) or fresh_val <= 0 or stored_val <= 0:
                continue
            ratio = fresh_val / stored_val
            ratios.append(ratio)

    if not ratios:
        return False, "NO_OVERLAP_DATA", None, 0

    mean_r = sum(ratios) / len(ratios)
    should_refetch, status = evaluate_overlap_window(symbol, ratios)

    if should_refetch:
        log_corporate_action_event(symbol, len(ratios), mean_r, status)

    return should_refetch, status, mean_r, len(ratios)

def log_corporate_action_event(
    symbol: str,
    overlap_count: int,
    detected_ratio: Optional[float],
    classification: str
):
    """Logs detected corporate action / retroactive adjustment to corporate_action_events audit table."""
    sql = """
    INSERT INTO corporate_action_events (symbol, detection_date, overlap_count, detected_ratio, classification)
    VALUES (?, DATE('now'), ?, ?, ?);
    """
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (symbol, overlap_count, detected_ratio, classification))

def fetch_and_store_stock_prices(
    symbols: Optional[List[str]] = None,
    period: str = "max",
    batch_size: int = PRICE_BATCH_SIZE,
    force_refetch: bool = False,
    now: Optional[datetime.datetime] = None
) -> int:
    """Downloads stock price history (incrementally or full backfill) for symbols and stores in price_history."""
    if not symbols:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol FROM stocks WHERE symbol NOT LIKE 'DUMMY%';")
            symbols = [row["symbol"] for row in cursor.fetchall()]

    symbols = [s for s in symbols if not s.startswith("DUMMY")]

    if not symbols:
        logger.warning("No valid stock symbols found to fetch prices.")
        return 0

    symbols_to_fetch = []
    cached_count = 0

    for sym in symbols:
        if not force_refetch and is_stock_price_cached(sym, now=now):
            cached_count += 1
        else:
            symbols_to_fetch.append(sym)

    logger.info(f"Price Cache Status: {cached_count} stocks FRESH (skipped), {len(symbols_to_fetch)} stocks NEED FETCH.")

    if not symbols_to_fetch:
        logger.info("All target stock prices are fresh in SQLite. 0 yfinance downloads required!")
        return 0

    total_records = 0
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

    full_backfill_syms = []
    incremental_syms_map: Dict[str, datetime.date] = {}

    for sym in symbols_to_fetch:
        stored_max = None if force_refetch else get_stored_max_date(sym)
        if stored_max is None:
            full_backfill_syms.append(sym)
        else:
            incremental_syms_map[sym] = stored_max

    if full_backfill_syms:
        total_batches = (len(full_backfill_syms) + batch_size - 1) // batch_size
        logger.info(f"Full backfill required for {len(full_backfill_syms)} new/forced stocks across {total_batches} batches ({batch_size} stocks/batch)...")
        
        for b_idx, i in enumerate(range(0, len(full_backfill_syms), batch_size), start=1):
            batch = full_backfill_syms[i : i + batch_size]
            formatted_map = {format_ticker_nse(s): s for s in batch}
            formatted_tickers = list(formatted_map.keys())
            
            logger.info(f"[PROGRESS] [Price Ingestion] Fetching full backfill batch {b_idx}/{total_batches} ({len(batch)} stocks: {batch[0]}..{batch[-1]})...")
            
            try:
                df = yf.download(
                    formatted_tickers,
                    period=period,
                    auto_adjust=False,
                    group_by="ticker",
                    progress=False,
                    threads=True
                )
                upserted = _process_and_upsert_yf_df(df, batch, formatted_map, upsert_sql)
                total_records += upserted
                logger.info(f"  ↳ Batch {b_idx}/{total_batches} complete: {upserted} records upserted.")
            except Exception as e:
                logger.error(f"Error fetching full backfill batch {batch}: {e}")

    if incremental_syms_map:
        inc_symbols = list(incremental_syms_map.keys())
        total_batches = (len(inc_symbols) + batch_size - 1) // batch_size
        logger.info(f"Incremental fetch required for {len(inc_symbols)} existing stocks across {total_batches} batches ({batch_size} stocks/batch)...")
        
        for b_idx, i in enumerate(range(0, len(inc_symbols), batch_size), start=1):
            batch = inc_symbols[i : i + batch_size]
            formatted_map = {format_ticker_nse(s): s for s in batch}
            formatted_tickers = list(formatted_map.keys())
            
            # Fetch with overlap window (min stored date minus ~35 calendar days / ~20 trading days)
            min_stored_date = min(incremental_syms_map[s] for s in batch)
            start_date = min_stored_date - datetime.timedelta(days=35)
            
            logger.info(f"[PROGRESS] [Price Ingestion] Fetching incremental batch {b_idx}/{total_batches} ({len(batch)} stocks: {batch[0]}..{batch[-1]}) from {start_date} (including overlap window)...")
            
            try:
                df = yf.download(
                    formatted_tickers,
                    start=start_date.strftime("%Y-%m-%d"),
                    auto_adjust=False,
                    group_by="ticker",
                    progress=False,
                    threads=True
                )

                if not df.empty:
                    # Perform Corporate Action / Retroactive Scale Shift Check across overlap window
                    refetch_symbols: Set[str] = set()

                    for yf_ticker, raw_symbol in formatted_map.items():
                        # Extract fresh adj_close series for this stock
                        if len(batch) == 1:
                            ticker_df = df
                            if isinstance(ticker_df.columns, pd.MultiIndex):
                                ticker_df.columns = ticker_df.columns.get_level_values(0)
                        else:
                            if yf_ticker not in df.columns.levels[0]:
                                continue
                            ticker_df = df[yf_ticker]

                        if "Adj Close" in ticker_df.columns:
                            fresh_series = ticker_df["Adj Close"]
                            should_refetch, status, ratio, count = detect_corporate_action_mismatch(raw_symbol, fresh_series)
                            if should_refetch:
                                refetch_symbols.add(raw_symbol)

                    # Trigger full refetches for any stock flagged with a scale shift
                    if refetch_symbols:
                        logger.info(f"Triggering full historical refetch for {len(refetch_symbols)} stocks due to detected scale shifts: {sorted(list(refetch_symbols))}")
                        for ref_sym in refetch_symbols:
                            ref_ticker = format_ticker_nse(ref_sym)
                            try:
                                ref_df = yf.download(ref_ticker, period=period, auto_adjust=False, progress=False)
                                if not ref_df.empty:
                                    with db_manager.get_connection() as conn:
                                        conn.cursor().execute("DELETE FROM price_history WHERE symbol = ?;", (ref_sym,))
                                    ref_upserted = _process_and_upsert_yf_df(ref_df, [ref_sym], {ref_ticker: ref_sym}, upsert_sql)
                                    total_records += ref_upserted
                                    logger.info(f"  ↳ Full refetch complete for {ref_sym}: {ref_upserted} price records replaced.")
                            except Exception as ref_err:
                                logger.error(f"Error executing full refetch for {ref_sym}: {ref_err}")

                    # Process remaining normal incremental stocks in the batch
                    normal_batch = [s for s in batch if s not in refetch_symbols]
                    if normal_batch:
                        normal_map = {format_ticker_nse(s): s for s in normal_batch}
                        upserted = _process_and_upsert_yf_df(df, normal_batch, normal_map, upsert_sql)
                        total_records += upserted
                        logger.info(f"  ↳ Batch {b_idx}/{total_batches} complete: {upserted} price records upserted for {len(normal_batch)} normal stocks.")
            except Exception as e:
                logger.error(f"Error fetching incremental batch {batch}: {e}")

    logger.info(f"Completed price ingestion. Total records upserted: {total_records}")
    update_listing_dates_proxy()
    return total_records

def _process_and_upsert_yf_df(df: pd.DataFrame, batch: List[str], formatted_map: Dict[str, str], upsert_sql: str) -> int:
    """Helper to parse yfinance multi-ticker or single-ticker DataFrame and upsert records into SQLite."""
    if df.empty:
        return 0

    records_to_insert = []
    if len(batch) == 1:
        symbol = batch[0]
        ticker_df = df
        if isinstance(ticker_df.columns, pd.MultiIndex):
            ticker_df.columns = ticker_df.columns.get_level_values(0)
        for date_idx, row in ticker_df.iterrows():
            if pd.isna(row.get("Close")) and pd.isna(row.get("Adj Close")):
                continue
            date_str = date_idx.strftime("%Y-%m-%d")
            adj_c = float(row["Adj Close"]) if pd.notna(row.get("Adj Close")) else float(row["Close"])
            records_to_insert.append((
                symbol, date_str,
                float(row["Open"]) if pd.notna(row.get("Open")) else None,
                float(row["High"]) if pd.notna(row.get("High")) else None,
                float(row["Low"]) if pd.notna(row.get("Low")) else None,
                float(row["Close"]) if pd.notna(row.get("Close")) else None,
                adj_c,
                int(row["Volume"]) if pd.notna(row.get("Volume")) else 0
            ))
    else:
        for yf_ticker, raw_symbol in formatted_map.items():
            if yf_ticker not in df.columns.levels[0]:
                continue
            ticker_df = df[yf_ticker]
            for date_idx, row in ticker_df.iterrows():
                if pd.isna(row.get("Close")) and pd.isna(row.get("Adj Close")):
                    continue
                date_str = date_idx.strftime("%Y-%m-%d")
                adj_c = float(row["Adj Close"]) if pd.notna(row.get("Adj Close")) else float(row["Close"])
                records_to_insert.append((
                    raw_symbol, date_str,
                    float(row["Open"]) if pd.notna(row.get("Open")) else None,
                    float(row["High"]) if pd.notna(row.get("High")) else None,
                    float(row["Low"]) if pd.notna(row.get("Low")) else None,
                    float(row["Close"]) if pd.notna(row.get("Close")) else None,
                    adj_c,
                    int(row["Volume"]) if pd.notna(row.get("Volume")) else 0
                ))

    if records_to_insert:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(upsert_sql, records_to_insert)
        return len(records_to_insert)

    return 0

def update_listing_dates_proxy() -> int:
    """Updates stocks.listing_date using earliest date in price_history."""
    sql = """
    UPDATE stocks
    SET listing_date = (
        SELECT MIN(date)
        FROM price_history
        WHERE price_history.symbol = stocks.symbol
    )
    WHERE EXISTS (
        SELECT 1 FROM price_history WHERE price_history.symbol = stocks.symbol
    );
    """
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        updated_rows = cursor.rowcount
    logger.info(f"Updated listing_date proxy for {updated_rows} stocks.")
    return updated_rows

if __name__ == "__main__":
    fetch_and_store_benchmark(period="max")
