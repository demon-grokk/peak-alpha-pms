"""
Filter 2: All-Time High TTM Profit (ex-exceptional items) Detector for Peak Alpha PMS.
Implements explicit exceptional item subtraction, current 4-quarter TTM continuity check,
and two-signal heuristic fallback.
Routes stocks with missing current window data or flagged heuristics to needs_manual_review = True.
"""
from typing import Optional, Dict, Any, List
import pandas as pd
from config.constants import TTM_PAT_MAX_QUARTER_GAP_DAYS
from src.db.database import db_manager
from src.filters.base import BaseFilter, FilterResult
from src.utils.helpers import is_ath_with_epsilon
from src.utils.logger import logger

class ATHProfitFilter(BaseFilter):
    def __init__(self, heuristic_discrepancy_ratio: float = 2.0, revenue_ath_threshold: float = 0.80):
        self.heuristic_discrepancy_ratio = heuristic_discrepancy_ratio
        self.revenue_ath_threshold = revenue_ath_threshold

    def evaluate(self, symbol: str, run_date: Optional[str] = None) -> FilterResult:
        """
        Evaluates whether a stock TTM PAT (ex-exceptional items) is at its historical maximum.
        Updates fundamentals_ttm table and routes missing/flagged data to needs_manual_review = True.
        """
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_financial_sector FROM stocks WHERE symbol = ?;", (symbol,))
            stock_row = cursor.fetchone()
            is_fin = stock_row["is_financial_sector"] if stock_row else 0

        date_filter = "AND quarter_end <= ?" if run_date else ""
        sql = f"""
            SELECT quarter_end, reported_pat, exceptional_items, pat_normalized, revenue, ebitda
            FROM fundamentals_quarterly
            WHERE symbol = ? {date_filter}
            ORDER BY quarter_end ASC;
        """
        params = [symbol, run_date] if run_date else [symbol]

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        if len(rows) < 4:
            self._upsert_ttm_state(symbol, None, None, is_ath=False, needs_review=True, outcome="Insufficient quarterly history (< 4 quarters)")
            return FilterResult(
                symbol=symbol,
                passed=False,
                metrics={"quarter_count": len(rows), "needs_manual_review": True},
                reason=f"Insufficient quarterly history ({len(rows)} quarters < 4 required). Routed to manual review."
            )

        ttm_series: List[Dict[str, Any]] = []
        valid_historical_ttms: List[Dict[str, Any]] = []

        for i in range(3, len(rows)):
            q_window = rows[i-3 : i+1]
            quarter_end = rows[i]["quarter_end"]
            
            # Check 4-quarter continuity for this specific 4-quarter window
            dt_list = [pd.to_datetime(r["quarter_end"]) for r in q_window]
            is_continuous = True
            gap_reason = None
            for k in range(1, len(dt_list)):
                diff_days = (dt_list[k] - dt_list[k-1]).days
                if diff_days > TTM_PAT_MAX_QUARTER_GAP_DAYS: # Gap larger than max allowable quarter gap
                    is_continuous = False
                    d1 = dt_list[k-1].strftime("%Y-%m-%d")
                    d2 = dt_list[k].strftime("%Y-%m-%d")
                    gap_reason = f"Missing quarter gap between {d1} and {d2} ({diff_days} days)"
                    break

            ttm_pat = sum(float(r["reported_pat"] if is_fin else r["pat_normalized"]) for r in q_window)
            ttm_rev = sum(float(r["revenue"]) for r in q_window if r["revenue"] is not None)
            ttm_ebitda = sum(float(r["ebitda"]) for r in q_window if r["ebitda"] is not None)

            window_data = {
                "quarter_end": quarter_end,
                "ttm_pat": ttm_pat,
                "ttm_revenue": ttm_rev,
                "ttm_ebitda": ttm_ebitda,
                "is_continuous": is_continuous,
                "gap_reason": gap_reason
            }

            ttm_series.append(window_data)
            if is_continuous:
                valid_historical_ttms.append(window_data)

        # Check LATEST (current) 4-quarter TTM window specifically
        latest_window = ttm_series[-1]
        if not latest_window["is_continuous"]:
            self._upsert_ttm_state(symbol, latest_window["quarter_end"], latest_window["ttm_pat"], is_ath=False, needs_review=True, outcome=latest_window["gap_reason"])
            return FilterResult(
                symbol=symbol,
                passed=False,
                metrics={"current_ttm_pat": latest_window["ttm_pat"], "needs_manual_review": True},
                reason=f"Current TTM window has discontinuous quarters ({latest_window['gap_reason']}). Routed to manual review."
            )

        current_ttm_pat = latest_window["ttm_pat"]
        as_of_quarter = latest_window["quarter_end"]

        # Use only valid continuous historical TTM windows for max comparison
        if not valid_historical_ttms:
            self._upsert_ttm_state(symbol, as_of_quarter, current_ttm_pat, is_ath=False, needs_review=True, outcome="No valid continuous historical TTM windows")
            return FilterResult(
                symbol=symbol,
                passed=False,
                metrics={"current_ttm_pat": current_ttm_pat, "needs_manual_review": True},
                reason="No valid continuous historical TTM windows found. Routed to manual review."
            )

        max_historical_ttm_pat = max(s["ttm_pat"] for s in valid_historical_ttms)

        if max_historical_ttm_pat <= 0:
            self._upsert_ttm_state(symbol, as_of_quarter, current_ttm_pat, is_ath=False, needs_review=False, outcome="Historical TTM PAT non-positive")
            return FilterResult(
                symbol=symbol,
                passed=False,
                metrics={"current_ttm_pat": current_ttm_pat, "max_historical_ttm_pat": max_historical_ttm_pat},
                reason="Max historical TTM PAT is non-positive (<= 0)."
            )

        is_ath_profit = is_ath_with_epsilon(current_ttm_pat, max_historical_ttm_pat)

        needs_manual_review = False
        review_outcome = None

        if is_ath_profit and len(valid_historical_ttms) >= 2:
            prev_year_ttm = valid_historical_ttms[-5] if len(valid_historical_ttms) >= 5 else valid_historical_ttms[0]
            pat_growth = (current_ttm_pat - prev_year_ttm["ttm_pat"]) / abs(prev_year_ttm["ttm_pat"]) if prev_year_ttm["ttm_pat"] else 0
            rev_growth = (latest_window["ttm_revenue"] - prev_year_ttm["ttm_revenue"]) / abs(prev_year_ttm["ttm_revenue"]) if prev_year_ttm["ttm_revenue"] else 0

            signal_a = (pat_growth > (rev_growth * self.heuristic_discrepancy_ratio)) if rev_growth > 0 else (pat_growth > 0.5)

            max_rev = max(s["ttm_revenue"] for s in valid_historical_ttms)
            signal_b = (latest_window["ttm_revenue"] < (max_rev * self.revenue_ath_threshold)) if max_rev > 0 else False

            if signal_a and signal_b:
                needs_manual_review = True
                review_outcome = "Heuristic Flag: PAT growth far out of proportion to Revenue ATH discrepancy."

        passed = is_ath_profit and not needs_manual_review

        self._upsert_ttm_state(
            symbol,
            as_of_quarter,
            current_ttm_pat,
            is_ath=is_ath_profit,
            needs_review=needs_manual_review,
            outcome=review_outcome
        )

        metrics = {
            "current_ttm_pat": current_ttm_pat,
            "ath_ttm_pat": max_historical_ttm_pat,
            "as_of_quarter": as_of_quarter,
            "is_financial_sector": is_fin,
            "needs_manual_review": needs_manual_review,
            "review_outcome": review_outcome
        }

        reason = (
            f"At All-Time High TTM Profit ({current_ttm_pat/1e7:.2f} Cr)."
            if passed else
            (f"Flagged for manual review: {review_outcome}" if needs_manual_review else f"Below ATH TTM PAT (Current: {current_ttm_pat/1e7:.2f} Cr, ATH: {max_historical_ttm_pat/1e7:.2f} Cr)")
        )

        return FilterResult(symbol=symbol, passed=passed, metrics=metrics, reason=reason)

    def _upsert_ttm_state(self, symbol: str, as_of_quarter: Optional[str], ttm_pat: Optional[float], is_ath: bool, needs_review: bool, outcome: Optional[str]):
        sql = """
        INSERT INTO fundamentals_ttm (symbol, as_of_quarter, ttm_pat_normalized, is_ath_flag, last_verified_ttm_pat, needs_manual_review, review_outcome)
        VALUES (?, ?, ?, ?, DATE('now'), ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            as_of_quarter = excluded.as_of_quarter,
            ttm_pat_normalized = excluded.ttm_pat_normalized,
            is_ath_flag = excluded.is_ath_flag,
            last_verified_ttm_pat = DATE('now'),
            needs_manual_review = excluded.needs_manual_review,
            review_outcome = excluded.review_outcome;
        """
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (symbol, as_of_quarter, ttm_pat, 1 if is_ath else 0, 1 if needs_review else 0, outcome))
