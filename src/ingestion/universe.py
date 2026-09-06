"""
Universe ingestion module for Peak Alpha PMS.
Fetches and updates Nifty Total Market constituents into SQLite.
"""
import io
import requests
import pandas as pd
from datetime import datetime
from config.settings import NIFTY_TOTAL_MARKET_CSV_URL
from src.db.database import db_manager
from src.utils.helpers import is_financial_sector
from src.utils.logger import logger

def fetch_nifty_total_market_universe() -> pd.DataFrame:
    """Downloads the live Nifty Total Market CSV list from NSE."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    logger.info(f"Fetching Nifty Total Market CSV from {NIFTY_TOTAL_MARKET_CSV_URL}...")
    response = requests.get(NIFTY_TOTAL_MARKET_CSV_URL, headers=headers, timeout=15)
    response.raise_for_status()
    
    df = pd.read_csv(io.StringIO(response.text))
    logger.info(f"Downloaded {len(df)} constituents from Nifty Total Market CSV.")
    return df

def update_universe_in_db() -> int:
    """Fetches live universe and upserts stock metadata into the database."""
    df = fetch_nifty_total_market_universe()
    
    upsert_sql = """
    INSERT INTO stocks (symbol, isin, name, sector, is_financial_sector, updated_at)
    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(symbol) DO UPDATE SET
        isin = excluded.isin,
        name = excluded.name,
        sector = excluded.sector,
        is_financial_sector = excluded.is_financial_sector,
        updated_at = CURRENT_TIMESTAMP;
    """
    
    count = 0
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        for _, row in df.iterrows():
            symbol = str(row["Symbol"]).strip().upper()
            company_name = str(row["Company Name"]).strip()
            industry = str(row["Industry"]).strip() if pd.notna(row["Industry"]) else ""
            isin = str(row["ISIN Code"]).strip() if pd.notna(row["ISIN Code"]) else ""
            is_fin = 1 if is_financial_sector(industry) else 0
            
            cursor.execute(upsert_sql, (symbol, isin, company_name, industry, is_fin))
            count += 1
            
    logger.info(f"Successfully upserted {count} stocks into database.")
    return count

if __name__ == "__main__":
    update_universe_in_db()
