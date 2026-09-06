"""
Manual Review Funnel module for Peak Alpha PMS.
Manages stocks routed to needs_manual_review = True due to missing data or exceptional item heuristic flags.
Generates Screener.in watchlist exports and logs review feedback back into SQLite.
"""
import os
import pandas as pd
from typing import Dict, Any, List
from config.settings import EXPORTS_DIR
from src.db.database import db_manager
from src.utils.logger import logger

class ManualReviewManager:
    @staticmethod
    def get_pending_reviews() -> List[Dict[str, Any]]:
        """Returns all stocks currently flagged with needs_manual_review = 1."""
        sql = """
            SELECT f.symbol, s.name, s.sector, f.as_of_quarter, f.ttm_pat_normalized, f.review_outcome
            FROM fundamentals_ttm f
            JOIN stocks s ON f.symbol = s.symbol
            WHERE f.needs_manual_review = 1;
        """
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def export_screener_watchlist(output_filename: str = "screener_manual_review_watchlist.csv") -> str:
        """
        Exports pending manual review stocks to Screener.in compatible watchlist CSV file in data/exports/.
        """
        reviews = ManualReviewManager.get_pending_reviews()
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        export_path = os.path.join(EXPORTS_DIR, output_filename)

        if not reviews:
            logger.info("No pending manual review stocks found to export.")
            df_empty = pd.DataFrame(columns=["NSE_Symbol", "Company_Name", "Sector", "Quarter_End", "TTM_PAT_Normalized", "Flag_Reason"])
            df_empty.to_csv(export_path, index=False)
            return export_path

        df = pd.DataFrame(reviews)
        df.rename(columns={
            "symbol": "NSE_Symbol",
            "name": "Company_Name",
            "sector": "Sector",
            "as_of_quarter": "Quarter_End",
            "ttm_pat_normalized": "TTM_PAT_Normalized",
            "review_outcome": "Flag_Reason"
        }, inplace=True)

        df.to_csv(export_path, index=False)
        logger.info(f"Exported {len(df)} manual review candidates to Screener.in watchlist file: {export_path}")
        return export_path

    @staticmethod
    def record_review_outcome(symbol: str, verified_ttm_pat: float, outcome_text: str, is_approved: bool) -> bool:
        """
        Logs human review result back into fundamentals_ttm table in SQLite.
        """
        sql = """
            UPDATE fundamentals_ttm
            SET needs_manual_review = 0,
                is_ath_flag = ?,
                ttm_pat_normalized = ?,
                last_verified_ttm_pat = DATE('now'),
                review_outcome = ?
            WHERE symbol = ?;
        """
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (1 if is_approved else 0, verified_ttm_pat, outcome_text, symbol))
            updated = cursor.rowcount > 0

        if updated:
            logger.info(f"Recorded manual review outcome for {symbol}: approved={is_approved}, verified_pat={verified_ttm_pat}")
        else:
            logger.warning(f"Failed to update manual review outcome for {symbol} (symbol not found in fundamentals_ttm).")
            
        return updated
