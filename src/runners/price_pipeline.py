"""
Calendar-Triggered Price Pipeline Runner for Peak Alpha PMS.
Fetches daily OHLCV prices for universe stocks and BSE 500 benchmark,
then evaluates Filter 1 (ATH Price) and Filter 3 Stage A (Relative Strength).
Implements exact market-close freshness checks and set-based bulk evaluation.
"""
from typing import List, Optional, Dict, Any
from src.db.database import db_manager
from src.ingestion.price_fetcher import fetch_and_store_stock_prices, fetch_and_store_benchmark
from src.filters.ath_price import ATHPriceFilter
from src.filters.relative_strength import RelativeStrengthFilter
from src.utils.logger import logger

class PricePipelineRunner:
    def __init__(self):
        self.ath_price_filter = ATHPriceFilter()
        self.rs_filter = RelativeStrengthFilter()

    def run(self, symbols: Optional[List[str]] = None, fetch_latest_prices: bool = True) -> Dict[str, Any]:
        """
        Executes calendar-clock price pipeline using set-based evaluation.
        """
        logger.info("=== Starting Calendar-Clock Price Pipeline ===")

        if fetch_latest_prices:
            logger.info("Checking & fetching latest benchmark prices for BSE 500...")
            fetch_and_store_benchmark()
            logger.info("Checking & fetching latest stock prices for universe...")
            fetch_and_store_stock_prices(symbols=symbols)

        if not symbols:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol FROM stocks WHERE symbol NOT LIKE 'DUMMY%';")
                symbols = [r["symbol"] for r in cursor.fetchall()]

        logger.info(f"Performing set-based evaluation of Filter 1 & Filter 3 Stage A for {len(symbols)} stocks...")
        
        # Set-based bulk ATH price evaluation
        f1_results = self.ath_price_filter.evaluate_universe(symbols=symbols)

        ath_passed_syms = []
        emerging_passed_syms = []
        rs_passed_syms = []
        both_passed_syms = []

        from src.ingestion.price_fetcher import get_fetch_upper_bound
        effective_run_date = get_fetch_upper_bound().strftime("%Y-%m-%d")

        # Clean prior run_date results for evaluated symbols only (prevents partial runs from wiping full scan)
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            if len(symbols) < 500:
                placeholders = ",".join(["?"] * len(symbols))
                params = [effective_run_date] + list(symbols)
                cursor.execute(f"DELETE FROM screen_results WHERE run_date = ? AND symbol IN ({placeholders});", params)
            else:
                cursor.execute("DELETE FROM screen_results WHERE run_date = ?;", (effective_run_date,))

        records = []
        total_syms = len(symbols)
        for idx, symbol in enumerate(symbols, start=1):
            f1_res = f1_results.get(symbol)
            f3_res = self.rs_filter.evaluate(symbol)

            is_ath_price = 1 if (f1_res and f1_res.metrics.get("is_ath_price")) else 0
            is_emerging = 1 if (f1_res and f1_res.metrics.get("is_emerging_candidate")) else 0
            near_ath_pct = f1_res.metrics.get("near_ath_price_pct") if f1_res else None
            outperforms_bse500 = 1 if f3_res.passed else 0

            if is_ath_price and not is_emerging:
                ath_passed_syms.append(symbol)
            if is_emerging:
                emerging_passed_syms.append(symbol)
            if outperforms_bse500:
                rs_passed_syms.append(symbol)
            if is_ath_price and outperforms_bse500:
                both_passed_syms.append(symbol)

            records.append((
                symbol,
                effective_run_date,
                is_ath_price,
                is_emerging,
                near_ath_pct,
                outperforms_bse500
            ))

        sql_insert = """
        INSERT INTO screen_results (symbol, run_date, is_ath_price, is_emerging_candidate, near_ath_price_pct, outperforms_bse500, outperforms_sector, passes_all_filters)
        VALUES (?, ?, ?, ?, ?, ?, NULL, 0);
        """

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(sql_insert, records)

        def _fmt(syms: List[str]) -> str:
            if not syms:
                return "0 = []"
            return f"{len(syms)} = {syms if len(syms) <= 25 else syms[:25] + [...]}"

        logger.info(f"=== Price Pipeline Filter Breakdown ===")
        logger.info(f"  - Total Universe Evaluated: {len(symbols)} stocks")
        logger.info(f"  - Passed Filter 1 Primary ATH Price (>=3yr): {_fmt(ath_passed_syms)}")
        logger.info(f"  - Passed Filter 1 Emerging Watchlist (1-3yr): {_fmt(emerging_passed_syms)}")
        logger.info(f"  - Passed Filter 3 Stage A (Outperforms BSE 500): {_fmt(rs_passed_syms)}")
        logger.info(f"  - Qualified Candidates (Passed Filter 1 + Filter 3 Stage A): {_fmt(both_passed_syms)}")

        return {
            "total_evaluated": len(symbols),
            "ath_passed": len(ath_passed_syms),
            "ath_passed_syms": ath_passed_syms,
            "emerging_passed": len(emerging_passed_syms),
            "emerging_passed_syms": emerging_passed_syms,
            "rs_passed": len(rs_passed_syms),
            "rs_passed_syms": rs_passed_syms,
            "both_passed": len(both_passed_syms),
            "both_passed_syms": both_passed_syms
        }

if __name__ == "__main__":
    runner = PricePipelineRunner()
    runner.run(symbols=["RELIANCE", "TCS", "HDFCBANK"], fetch_latest_prices=False)
