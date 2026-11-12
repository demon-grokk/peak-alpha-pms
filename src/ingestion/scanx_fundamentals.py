"""
ScanX / Dhan API Quarterly Fundamentals Ingestion Module for Peak Alpha PMS.
Pulls continuous 46+ quarter financial statements, valuation, ROCE/ROE, market cap classification,
BSE security codes, and shareholding metrics.
Implements smart cache expiry checks (skips API hits if fresh current quarter data exists),
batch multi-ISIN API requests, and graceful token expiry handling.
"""
import time
import datetime
import requests
import pandas as pd
from typing import List, Optional, Dict, Any
from config.constants import (
    SCANX_API_URL,
    SCANX_BATCH_SIZE,
    SCANX_CACHE_MAX_AGE_DAYS,
)
from config.settings import SCANX_AUTH_TOKEN
from src.db.database import db_manager
from src.utils.logger import logger

def yyyymm_to_iso_date(yyyymm: str) -> str:
    """Converts YYYYMM (e.g. 202606) to ISO quarter end date string (e.g. 2026-06-30)."""
    if len(yyyymm) != 6:
        return yyyymm
    year = int(yyyymm[:4])
    month = int(yyyymm[4:6])
    if month in [3, 4]:
        return f"{year:04d}-03-31"
    elif month in [6, 5]:
        return f"{year:04d}-06-30"
    elif month in [9, 8]:
        return f"{year:04d}-09-30"
    elif month in [12, 11]:
        return f"{year:04d}-12-31"
    return f"{year:04d}-{month:02d}-28"

def get_expected_latest_quarter_end(ref_date: Optional[datetime.date] = None) -> str:
    """Determines expected latest ended quarter ISO date for Indian fiscal quarters."""
    if ref_date is None:
        ref_date = datetime.date.today()
    y, m = ref_date.year, ref_date.month
    # Earnings for quarter ending month M are typically reported in month M+1 to M+2
    if m in [1, 2, 3]:
        return f"{y-1:04d}-12-31"
    elif m in [4, 5, 6]:
        return f"{y:04d}-03-31"
    elif m in [7, 8, 9]:
        return f"{y:04d}-06-30"
    else: # 10, 11, 12
        return f"{y:04d}-09-30"

def is_stock_fundamentals_cached(symbol: str) -> bool:
    """
    Checks if stock fundamentals in SQLite are fresh and up-to-date.
    Returns True if latest quarter data is cached and last_result_date is < 60 days old.
    """
    expected_q = get_expected_latest_quarter_end()
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT quarter_end, last_result_date
            FROM fundamentals_quarterly
            WHERE symbol = ?
            ORDER BY quarter_end DESC
            LIMIT 1;
        """, (symbol,))
        row = cursor.fetchone()

    if not row or not row["quarter_end"]:
        return False

    latest_q = row["quarter_end"]
    last_fetch = row["last_result_date"]

    # If cached latest quarter matches or is newer than expected_q, cache is valid!
    if latest_q >= expected_q:
        return True

    # Alternatively if fetched within last 30 days, avoid spamming API
    if last_fetch:
        try:
            fetch_dt = datetime.datetime.strptime(last_fetch, "%Y-%m-%d").date()
            if (datetime.date.today() - fetch_dt).days < 30:
                return True
        except ValueError:
            pass

    return False

def fetch_and_store_scanx_fundamentals(symbols: Optional[List[str]] = None, batch_size: int = SCANX_BATCH_SIZE, force_refetch: bool = False) -> int:
    """
    Pulls quarterly fundamentals via ScanX API for specified stocks (or full universe).
    Checks cache before hitting API. Batches ISINs into HTTP POST calls.
    """
    if not SCANX_AUTH_TOKEN:
        logger.info("SCANX_AUTH_TOKEN not provided; utilizing open-web public endpoint.")

    # Query ISIN mapping from stocks table
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        if symbols:
            placeholders = ",".join(["?"] * len(symbols))
            cursor.execute(f"SELECT symbol, isin FROM stocks WHERE symbol IN ({placeholders}) AND isin IS NOT NULL AND isin != '';", symbols)
        else:
            cursor.execute("SELECT symbol, isin FROM stocks WHERE isin IS NOT NULL AND isin != '';")
        rows = cursor.fetchall()

    if not rows:
        logger.warning("No stocks with valid ISIN found for ScanX fundamentals ingestion.")
        return 0

    isin_to_symbol = {r["isin"]: r["symbol"] for r in rows}
    
    # Smart Cache Expiry Check: filter out stocks with valid cache unless force_refetch=True
    isins_to_fetch = []
    cached_count = 0

    for isin, sym in isin_to_symbol.items():
        if not force_refetch and is_stock_fundamentals_cached(sym):
            cached_count += 1
        else:
            isins_to_fetch.append(isin)

    logger.info(f"ScanX Fundamentals Cache Status: {cached_count} stocks FRESH (skipped), {len(isins_to_fetch)} stocks NEED FETCH.")

    if not isins_to_fetch:
        logger.info("All target stocks have fresh fundamentals cached in SQLite. 0 ScanX API hits required!")
        return cached_count

    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://scanx.trade",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
    }
    if SCANX_AUTH_TOKEN:
        headers["auth"] = SCANX_AUTH_TOKEN

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

    update_stock_metrics_sql = """
    UPDATE stocks
    SET market_cap = ?,
        pe_ratio = ?,
        pb_ratio = ?,
        book_value = ?,
        dividend_yield = ?,
        face_value = ?,
        bse_code = ?,
        market_cap_category = ?,
        sub_sector = ?,
        roce = ?,
        roe = ?,
        promoter_holding = ?,
        fii_holding = ?,
        dii_holding = ?,
        updated_at = CURRENT_TIMESTAMP
    WHERE symbol = ?;
    """

    upsert_ttm_review_sql = """
    INSERT INTO fundamentals_ttm (symbol, needs_manual_review, review_outcome)
    VALUES (?, 1, ?)
    ON CONFLICT(symbol) DO UPDATE SET
        needs_manual_review = 1,
        review_outcome = excluded.review_outcome;
    """

    processed_count = 0
    total_batches = (len(isins_to_fetch) + batch_size - 1) // batch_size

    for batch_idx, i in enumerate(range(0, len(isins_to_fetch), batch_size), start=1):
        batch_isins = isins_to_fetch[i : i + batch_size]
        batch_syms = [isin_to_symbol[isin] for isin in batch_isins]
        payload = {"data": {"isins": batch_isins}}

        logger.info(f"[PROGRESS] [Batch {batch_idx}/{total_batches}] Hitting ScanX API for {len(batch_isins)} stocks: {batch_syms[:5]}...")

        try:
            resp = requests.post(SCANX_API_URL, headers=headers, json=payload, timeout=15)
            
            if resp.status_code in [401, 403]:
                logger.error("[CRITICAL WARNING] ScanX API Authentication Token expired or unauthorized! Please update SCANX_AUTH_TOKEN in .env file.")
                for isin in batch_isins:
                    sym = isin_to_symbol[isin]
                    with db_manager.get_connection() as conn:
                        conn.cursor().execute(upsert_ttm_review_sql, (sym, "ScanX API Token Expired (401/403)"))
                continue

            if resp.status_code != 200:
                logger.warning(f"ScanX API returned HTTP {resp.status_code} for batch. Tagging manual review.")
                for isin in batch_isins:
                    sym = isin_to_symbol[isin]
                    with db_manager.get_connection() as conn:
                        conn.cursor().execute(upsert_ttm_review_sql, (sym, f"ScanX API HTTP {resp.status_code}"))
                continue

            res_data = resp.json()
            if res_data.get("status") != "success" or "data" not in res_data:
                logger.warning(f"ScanX API response error: {res_data.get('message')}. Tagging manual review.")
                continue

            items = res_data["data"]
            for item in items:
                isin = item.get("isin")
                symbol = isin_to_symbol.get(isin)
                if not symbol:
                    continue

                # 1. Parse Consolidated or Standalone Quarterly Financials
                cq = item.get("incomeStat_cq") or item.get("incomeStat_sq") or {}
                if cq and "YEAR" in cq and "NET_PROFIT" in cq:
                    years = cq["YEAR"].split("|")
                    net_profits = cq["NET_PROFIT"].split("|")
                    revenues = cq.get("REVENUE", "").split("|")
                    ebitdas = cq.get("EBITDA", "").split("|")

                    records = []
                    for idx, y_str in enumerate(years):
                        if not y_str:
                            continue
                        q_date = yyyymm_to_iso_date(y_str)
                        
                        try:
                            pat_cr = float(net_profits[idx]) if idx < len(net_profits) and net_profits[idx] != "" else 0.0
                            pat_abs = pat_cr * 1e7
                        except ValueError:
                            continue

                        rev_cr = float(revenues[idx]) if idx < len(revenues) and revenues[idx] != "" else None
                        rev_abs = rev_cr * 1e7 if rev_cr is not None else None

                        eb_cr = float(ebitdas[idx]) if idx < len(ebitdas) and ebitdas[idx] != "" else None
                        eb_abs = eb_cr * 1e7 if eb_cr is not None else None

                        records.append((symbol, q_date, pat_abs, 0.0, pat_abs, rev_abs, eb_abs))

                    if records:
                        with db_manager.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.executemany(upsert_q_sql, records)
                        processed_count += 1

                # 2. Parse Full Company Valuation (CV), Classification & Security Codes
                cv = item.get("CV", {})
                mcap = float(cv["MARKET_CAP"]) if cv.get("MARKET_CAP") else None
                pe = float(cv["STOCK_PE"]) if cv.get("STOCK_PE") else None
                pb = float(cv["PRICE_TO_BOOK_VALUE"]) if cv.get("PRICE_TO_BOOK_VALUE") else None
                bv = float(cv["BOOK_VALUE"]) if cv.get("BOOK_VALUE") else None
                div_yield = float(cv["DIVIDEND_YEILD"]) if cv.get("DIVIDEND_YEILD") else None
                face_val = float(cv["FACE_VALUE"]) if cv.get("FACE_VALUE") else None
                bse = cv.get("BSE") if cv.get("BSE") and cv.get("BSE") != "0" else None
                cap_cat = cv.get("COMPANY_CLASSIFICATION")
                sub_sec = cv.get("SUB_SECTOR") or cv.get("INDUSTRY_NAME")

                roce_roe = item.get("roce_roe", {})
                roce = float(roce_roe["ROCE"]) if roce_roe.get("ROCE") else None
                roe = float(roce_roe["ROE"]) if roce_roe.get("ROE") else None

                shp = item.get("sHp", {})
                promoter_list = shp.get("PROMOTER", "").split("|")
                fii_list = shp.get("FII", "").split("|")
                dii_list = shp.get("DII", "").split("|")

                promoter = float(promoter_list[0]) if promoter_list and promoter_list[0] != "" else None
                fii = float(fii_list[0]) if fii_list and fii_list[0] != "" else None
                dii = float(dii_list[0]) if dii_list and dii_list[0] != "" else None

                with db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(update_stock_metrics_sql, (
                        mcap, pe, pb, bv, div_yield, face_val, bse, cap_cat, sub_sec,
                        roce, roe, promoter, fii, dii, symbol
                    ))

        except Exception as e:
            logger.error(f"Exception during ScanX API fetch: {e}")

    logger.info(f"ScanX fundamentals ingestion completed. Fresh: {cached_count}, Fetched: {processed_count}.")
    return processed_count + cached_count

if __name__ == "__main__":
    fetch_and_store_scanx_fundamentals(["RELIANCE", "TCS", "HDFCBANK"])
