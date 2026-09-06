"""
Quarterly fundamentals ingestion module for Peak Alpha PMS.
Pulls quarterly PAT, exceptional items, revenue, and EBITDA via yfinance.
Routes stocks with missing/patchy data directly to needs_manual_review = True.
"""
import yfinance as yf
import pandas as pd
from typing import List, Optional
from src.db.database import db_manager
from src.utils.helpers import format_ticker_nse
from src.utils.logger import logger

def fetch_and_store_fundamentals(symbols: Optional[List[str]] = None) -> int:
    """
    Pulls quarterly financials for specified stocks (or full universe) and stores in DB.
    Enforces Rule #3: Missing data routes directly to needs_manual_review = True.
    """
    if not symbols:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol FROM stocks;")
            symbols = [row["symbol"] for row in cursor.fetchall()]

    if not symbols:
        logger.warning("No stock symbols provided for fundamentals ingestion.")
        return 0

    logger.info(f"Starting fundamentals ingestion for {len(symbols)} stocks...")
    processed_count = 0

    upsert_q_sql = """
    INSERT INTO fundamentals_quarterly (symbol, quarter_end, reported_pat, exceptional_items, pat_normalized, revenue, ebitda, last_result_date)
    VALUES (?, ?, ?, ?, ?, ?, ?, DATE('now'))
    ON CONFLICT(symbol, quarter_end) DO UPDATE SET
        reported_pat = excluded.reported_pat,
        exceptional_items = excluded.exceptional_items,
        pat_normalized = excluded.pat_normalized,
        revenue = excluded.revenue,
        ebitda = excluded.ebitda,
        last_result_date = DATE('now');
    """

    upsert_ttm_review_sql = """
    INSERT INTO fundamentals_ttm (symbol, needs_manual_review, review_outcome)
    VALUES (?, 1, ?)
    ON CONFLICT(symbol) DO UPDATE SET
        needs_manual_review = 1,
        review_outcome = excluded.review_outcome;
    """

    for symbol in symbols:
        yf_ticker = format_ticker_nse(symbol)
        try:
            t = yf.Ticker(yf_ticker)
            qf = t.quarterly_financials

            if qf is None or qf.empty:
                logger.warning(f"No quarterly financials data returned for {symbol}. Tagging needs_manual_review = True.")
                with db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(upsert_ttm_review_sql, (symbol, "Missing yfinance quarterly data"))
                continue

            records = []
            cols = qf.columns

            for col in cols:
                quarter_str = col.strftime("%Y-%m-%d")
                
                # Find PAT / Net Income
                reported_pat = None
                for pat_key in ["Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations", "Net Income From Continuing Operation Net Minority Interest"]:
                    if pat_key in qf.index and pd.notna(qf.loc[pat_key, col]):
                        reported_pat = float(qf.loc[pat_key, col])
                        break
                        
                # Find Exceptional Items / Unusual Items
                exceptional_items = 0.0
                for exc_key in ["Total Unusual Items", "Total Unusual Items Excluding Goodwill", "Tax Effect Of Unusual Items"]:
                    if exc_key in qf.index and pd.notna(qf.loc[exc_key, col]):
                        exceptional_items = float(qf.loc[exc_key, col])
                        break
                        
                # Find Revenue
                revenue = None
                for rev_key in ["Total Revenue", "Operating Revenue", "Gross Profit"]:
                    if rev_key in qf.index and pd.notna(qf.loc[rev_key, col]):
                        revenue = float(qf.loc[rev_key, col])
                        break

                # Find EBITDA
                ebitda = None
                for eb_key in ["EBITDA", "Normalized EBITDA", "EBIT"]:
                    if eb_key in qf.index and pd.notna(qf.loc[eb_key, col]):
                        ebitda = float(qf.loc[eb_key, col])
                        break

                if reported_pat is None:
                    continue

                pat_normalized = reported_pat - exceptional_items

                records.append((
                    symbol,
                    quarter_str,
                    reported_pat,
                    exceptional_items,
                    pat_normalized,
                    revenue,
                    ebitda
                ))

            if not records:
                logger.warning(f"Could not parse valid quarterly PAT for {symbol}. Tagging needs_manual_review = True.")
                with db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(upsert_ttm_review_sql, (symbol, "Unparseable quarterly PAT"))
                continue

            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.executemany(upsert_q_sql, records)
                
            processed_count += 1

        except Exception as e:
            logger.error(f"Exception during fundamentals fetch for {symbol}: {e}. Tagging needs_manual_review = True.")
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(upsert_ttm_review_sql, (symbol, f"yfinance Exception: {str(e)}"))

    logger.info(f"Completed fundamentals ingestion. Successfully ingested quarterly data for {processed_count} stocks.")
    return processed_count

if __name__ == "__main__":
    fetch_and_store_fundamentals(["RELIANCE", "TCS", "HDFCBANK"])
