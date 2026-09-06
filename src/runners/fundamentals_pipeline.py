"""
Event-Triggered Fundamentals Pipeline Runner for Peak Alpha PMS.
Implements the Funnel Strategy:
Only fetches ScanX quarterly fundamentals and evaluates Filter 2 (ATH TTM PAT) for stocks
that ALREADY passed Filter 1 (ATH Price) and Filter 3 Stage A (Relative Strength).
Routes missing/flagged candidate stocks to needs_manual_review = True.
"""
from typing import List, Optional, Dict, Any
from src.db.database import db_manager
from src.ingestion.scanx_fundamentals import fetch_and_store_scanx_fundamentals
from src.filters.ath_profit import ATHProfitFilter
from src.core.manual_review import ManualReviewManager
from src.utils.logger import logger

class FundamentalsPipelineRunner:
    def __init__(self):
        self.ath_profit_filter = ATHProfitFilter()

    def run(self, symbols: Optional[List[str]] = None, fetch_latest_fundamentals: bool = True, funnel_only: bool = True) -> Dict[str, Any]:
        """
        Executes event-clock fundamentals pipeline.
        If funnel_only=True, restricts ScanX API hits and Filter 2 evaluation ONLY to stocks
        that passed Filter 1 (ATH Price) and Filter 3 Stage A (Relative Strength).
        """
        logger.info("=== Starting Event-Clock Fundamentals Pipeline (Funnel Strategy) ===")

        from src.ingestion.price_fetcher import get_fetch_upper_bound
        effective_run_date = get_fetch_upper_bound().strftime("%Y-%m-%d")

        # 1. Determine target symbols using Funnel Strategy
        if funnel_only and not symbols:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT symbol FROM screen_results
                    WHERE run_date = ?
                      AND is_ath_price = 1
                      AND outperforms_bse500 = 1;
                """, (effective_run_date,))
                qualified_rows = cursor.fetchall()
                symbols = [r["symbol"] for r in qualified_rows]
            
            logger.info(f"[FUNNEL SELECTION] {len(symbols)} stocks passed Filter 1 (ATH Price) & Filter 3 Stage A (Relative Strength): {symbols}")
            if not symbols:
                logger.info("[FUNNEL INFO] 0 stocks met price & relative strength conditions. 0 ScanX API hits required!")
                return {
                    "total_evaluated": 0,
                    "profit_passed": 0,
                    "profit_passed_syms": [],
                    "manual_reviews": 0,
                    "manual_reviews_syms": [],
                    "export_path": None
                }

        elif not symbols:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol FROM stocks WHERE symbol NOT LIKE 'DUMMY%';")
                symbols = [r["symbol"] for r in cursor.fetchall()]

        # 2. Fetch quarterly fundamentals for price-qualified candidates ONLY
        if fetch_latest_fundamentals:
            logger.info(f"Fetching ScanX fundamentals for {len(symbols)} qualified candidate stocks: {symbols}...")
            fetch_and_store_scanx_fundamentals(symbols=symbols)

        # 3. Evaluate Filter 2 (ATH TTM PAT) for qualified candidates
        logger.info(f"Evaluating Filter 2 (ATH TTM PAT) for {len(symbols)} qualified stocks...")
        profit_passed_syms = []
        manual_reviews_syms = []

        total_syms = len(symbols)
        for idx, symbol in enumerate(symbols, start=1):
            if idx == 1 or idx % 50 == 0 or idx == total_syms:
                logger.info(f"[PROGRESS] [Fundamentals Pipeline] Evaluating stock {idx}/{total_syms} ({symbol})...")

            f2_res = self.ath_profit_filter.evaluate(symbol)
            is_ath_profit = 1 if f2_res.passed else 0
            if is_ath_profit:
                profit_passed_syms.append(symbol)

            if f2_res.metrics.get("needs_manual_review"):
                manual_reviews_syms.append(symbol)

            # Update screen_results for effective_run_date
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE screen_results
                    SET is_ath_profit = ?,
                        passes_all_filters = (is_ath_price = 1 AND is_emerging_candidate = 0 AND ? = 1 AND outperforms_bse500 = 1)
                    WHERE symbol = ? AND run_date = ?;
                """, (is_ath_profit, is_ath_profit, symbol, effective_run_date))

        # Export pending manual reviews to Screener.in CSV
        export_path = ManualReviewManager.export_screener_watchlist()

        def _fmt(syms: List[str]) -> str:
            if not syms:
                return "0 = []"
            return f"{len(syms)} = {syms if len(syms) <= 25 else syms[:25] + [...]}"

        logger.info(f"=== Fundamentals Pipeline Analysis ===")
        logger.info(f"  - Qualified Candidates Evaluated: {len(symbols)} = {symbols}")
        logger.info(f"  - Passed Filter 2 (ATH TTM PAT): {_fmt(profit_passed_syms)}")
        logger.info(f"  - Routed to Manual Review: {_fmt(manual_reviews_syms)}")
        logger.info(f"  - Screener.in Watchlist Export: {export_path}")

        return {
            "total_evaluated": len(symbols),
            "profit_passed": len(profit_passed_syms),
            "profit_passed_syms": profit_passed_syms,
            "manual_reviews": len(manual_reviews_syms),
            "manual_reviews_syms": manual_reviews_syms,
            "export_path": export_path
        }

if __name__ == "__main__":
    runner = FundamentalsPipelineRunner()
    runner.run(symbols=["RELIANCE", "TCS", "HDFCBANK"], fetch_latest_fundamentals=False, funnel_only=False)
