"""
Allocation Pipeline Runner for Peak Alpha PMS.

Executes Layer 3 Allocation & Stop-Loss Engine across candidate stocks from
`screen_results` or explicit manual symbol overrides.
"""
from typing import List, Optional, Dict, Any
from src.db.database import db_manager
from src.execution.allocation_engine import AllocationEngine, AllocationResult
from src.utils.logger import logger

class AllocationRunner:
    def __init__(self, engine: Optional[AllocationEngine] = None):
        self.engine = engine or AllocationEngine()

    def run(
        self,
        symbols: Optional[List[str]] = None,
        capital_c: float = 1_000_000.0,
        available_cash: float = 1_000_000.0
    ) -> Dict[str, Any]:
        """
        Runs allocation evaluation for candidate symbols.
        - If symbols is None/empty: queries screen_results where passes_all_filters == 1 for latest date.
        - If symbols is provided: evaluates explicit symbols with selection_reason = 'MANUAL_EXPLORATORY'.
        """
        logger.info("==================================================")
        logger.info("      STARTING LAYER 3 ALLOCATION EVALUATION      ")
        logger.info("==================================================")

        if not symbols:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT sr.symbol
                    FROM screen_results sr
                    JOIN fundamentals_ttm ft ON sr.symbol = ft.symbol
                    WHERE sr.run_date = (SELECT MAX(run_date) FROM screen_results)
                      AND sr.is_ath_price = 1
                      AND sr.is_emerging_candidate = 0
                      AND sr.outperforms_bse500 = 1
                      AND ft.is_ath_flag = 1
                      AND (ft.needs_manual_review IS NULL OR ft.needs_manual_review = 0)
                    ORDER BY sr.symbol ASC;
                """)
                rows = cursor.fetchall()
                candidate_symbols = [r["symbol"] for r in rows]
                selection_reason = "PASSES_ALL_FILTERS"
        else:

            candidate_symbols = list(symbols)
            selection_reason = "MANUAL_EXPLORATORY"

        logger.info(f"Evaluating {len(candidate_symbols)} candidates (Selection Reason: {selection_reason})")
        logger.info(f"Portfolio Parameters: Capital_C = ₹{capital_c:,.2f}, Available Cash = ₹{available_cash:,.2f}")

        results: List[AllocationResult] = []
        approved_count = 0
        rejected_risk_count = 0
        insufficient_cash_count = 0
        current_cash = available_cash

        for sym in candidate_symbols:
            try:
                res = self.engine.evaluate_symbol_from_db(
                    symbol=sym,
                    capital_c=capital_c,
                    available_cash=current_cash,
                    selection_reason=selection_reason
                )

                # Persist to SQLite entry_evaluations
                self.engine.save_evaluation(res)
                results.append(res)

                if res.gate_decision == "APPROVED":
                    approved_count += 1
                    current_cash -= res.allocated_capital
                elif res.gate_decision == "REJECTED_RISK_GT_8PCT":
                    rejected_risk_count += 1
                elif res.gate_decision == "INSUFFICIENT_CASH":
                    insufficient_cash_count += 1

            except Exception as e:
                logger.error(f"Error evaluating allocation for symbol {sym}: {e}")

        logger.info("==================================================")
        logger.info("       ALLOCATION EVALUATION RUN COMPLETED        ")
        logger.info("==================================================")
        logger.info(f"Total Evaluated: {len(results)}")
        logger.info(f"Approved: {approved_count}")
        logger.info(f"Rejected (>8% Risk): {rejected_risk_count}")
        logger.info(f"Insufficient Cash: {insufficient_cash_count}")
        logger.info(f"Remaining Deployable Cash: ₹{current_cash:,.2f}")

        return {
            "total_evaluated": len(results),
            "approved_count": approved_count,
            "rejected_risk_count": rejected_risk_count,
            "insufficient_cash_count": insufficient_cash_count,
            "remaining_cash": current_cash,
            "results": results
        }

def run_allocation_evaluation(
    symbols: Optional[List[str]] = None,
    capital_c: float = 1_000_000.0,
    available_cash: float = 1_000_000.0
) -> Dict[str, Any]:
    """Helper runner entry point."""
    runner = AllocationRunner()
    return runner.run(symbols=symbols, capital_c=capital_c, available_cash=available_cash)
