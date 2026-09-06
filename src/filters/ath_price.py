"""
Filter 1: Two-Tier All-Time High Price Detector for Peak Alpha PMS.

Evaluates stock ATH price condition using set-based SQL queries.
Supports single-symbol evaluation or bulk set-based universe evaluation.
"""
from typing import Optional, Dict, List
from config.constants import (
    EPSILON,
    PRIMARY_ATH_MIN_TRADING_DAYS,
    EMERGING_ATH_MIN_TRADING_DAYS,
)
from src.db.database import db_manager
from src.filters.base import BaseFilter, FilterResult
from src.utils.helpers import is_ath_with_epsilon

class ATHPriceFilter(BaseFilter):
    def __init__(
        self,
        primary_min_days: int = PRIMARY_ATH_MIN_TRADING_DAYS,
        emerging_min_days: int = EMERGING_ATH_MIN_TRADING_DAYS,
        epsilon: float = EPSILON
    ):
        self.primary_min_days = primary_min_days
        self.emerging_min_days = emerging_min_days
        self.epsilon = epsilon

    def evaluate_universe(self, symbols: Optional[List[str]] = None, run_date: Optional[str] = None) -> Dict[str, FilterResult]:
        """
        Performs bulk set-based ATH evaluation for given symbols (or full DB universe).
        Automatically chunks large symbol lists into batches of 500 to scale safely to 5000+ stocks.
        """
        if symbols and len(symbols) > 500:
            results = {}
            for i in range(0, len(symbols), 500):
                chunk = symbols[i : i + 500]
                results.update(self.evaluate_universe(symbols=chunk, run_date=run_date))
            return results
        symbol_filter_sql_bare = ""
        symbol_filter_sql_p = ""
        symbol_params = []
        if symbols:
            placeholders = ",".join(["?"] * len(symbols))
            symbol_filter_sql_bare = f" AND symbol IN ({placeholders})"
            symbol_filter_sql_p = f" AND p.symbol IN ({placeholders})"
            symbol_params = list(symbols)

        sql = f"""
        WITH stock_baselines AS (
            SELECT symbol, COALESCE(ath_baseline_start_date, listing_date, '1900-01-01') AS start_date
            FROM stocks
        ),
        latest_dates AS (
            SELECT p.symbol, MAX(p.date) AS max_date
            FROM price_history p
            JOIN stock_baselines sb ON p.symbol = sb.symbol
            WHERE p.date >= sb.start_date AND (? IS NULL OR p.date <= ?){symbol_filter_sql_p}
            GROUP BY p.symbol
        ),
        latest_prices AS (
            SELECT p.symbol, p.date, p.close, p.adj_close AS current_adj_close
            FROM price_history p
            JOIN latest_dates l ON p.symbol = l.symbol AND p.date = l.max_date
        ),
        prior_ath AS (
            SELECT p.symbol, 
                   MAX(p.high * (p.adj_close / p.close)) AS max_prior_adj_high,
                   MAX(p.adj_close) AS max_prior_adj_close
            FROM price_history p
            JOIN stock_baselines sb ON p.symbol = sb.symbol
            JOIN latest_dates l ON p.symbol = l.symbol AND p.date < l.max_date
            WHERE p.date >= sb.start_date AND p.close IS NOT NULL AND p.close > 0{symbol_filter_sql_p}
            GROUP BY p.symbol
        ),
        total_counts AS (
            SELECT p.symbol, COUNT(*) AS trading_days_count
            FROM price_history p
            JOIN stock_baselines sb ON p.symbol = sb.symbol
            WHERE p.date >= sb.start_date AND p.close IS NOT NULL AND p.close > 0 AND (? IS NULL OR p.date <= ?){symbol_filter_sql_p}
            GROUP BY p.symbol
        )
        SELECT 
            lp.symbol,
            lp.current_adj_close,
            COALESCE(pa.max_prior_adj_high, lp.current_adj_close) AS max_historical_adj_high,
            COALESCE(pa.max_prior_adj_close, lp.current_adj_close) AS max_historical_adj_close,
            tc.trading_days_count,
            lp.date AS latest_date
        FROM latest_prices lp
        LEFT JOIN prior_ath pa ON lp.symbol = pa.symbol
        JOIN total_counts tc ON lp.symbol = tc.symbol;
        """
        params = [run_date, run_date] + symbol_params + symbol_params + [run_date, run_date] + symbol_params

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        symbol_set = set(symbols) if symbols else None
        results = {}

        for row in rows:
            symbol = row["symbol"]
            if symbol_set and symbol not in symbol_set:
                continue

            trading_days_count = row["trading_days_count"]
            current_price = float(row["current_adj_close"])
            max_historical_high = float(row["max_historical_adj_high"]) if row["max_historical_adj_high"] is not None else float(row["max_historical_adj_close"])
            latest_date = row["latest_date"]

            if trading_days_count < self.emerging_min_days:
                results[symbol] = FilterResult(
                    symbol=symbol,
                    passed=False,
                    metrics={
                        "trading_days_count": trading_days_count,
                        "is_ath_price": False,
                        "is_emerging_candidate": False,
                        "near_ath_price_pct": None  # SQL NULL for insufficient listing history (< 252 trading days)
                    },
                    reason=f"Insufficient history ({trading_days_count} trading days < {self.emerging_min_days} required)."
                )
                continue

            at_ath_price = is_ath_with_epsilon(current_price, max_historical_high, self.epsilon)

            # Continuous percentage distance to ATH (stored as a decimal, e.g., 0.05 = 5% below ATH).
            # If current price is at or above max prior high (within epsilon), distance is 0.0.
            if at_ath_price or max_historical_high <= 0:
                near_ath_price_pct = 0.0
            else:
                near_ath_price_pct = max(0.0, (max_historical_high - current_price) / max_historical_high)

            is_ath_price = at_ath_price
            is_emerging_candidate = at_ath_price and (trading_days_count < self.primary_min_days)

            if at_ath_price:
                if trading_days_count >= self.primary_min_days:
                    reason = f"At True All-Time High price (Primary List, {trading_days_count} days >= {self.primary_min_days})."
                else:
                    reason = f"At True All-Time High price (Emerging Candidate Watchlist, {trading_days_count} days)."
            else:
                reason = f"Below ATH (Current Close: {current_price:.2f}, Prior Max High: {max_historical_high:.2f})."

            passed = at_ath_price and (trading_days_count >= self.primary_min_days)

            metrics = {
                "current_adj_close": current_price,
                "ath_adj_close": max_historical_high,
                "latest_date": latest_date,
                "trading_days_count": trading_days_count,
                "is_ath_price": is_ath_price,
                "is_emerging_candidate": is_emerging_candidate,
                "near_ath_price_pct": near_ath_price_pct,
                "epsilon": self.epsilon
            }

            results[symbol] = FilterResult(symbol=symbol, passed=passed, metrics=metrics, reason=reason)

        return results

    def evaluate(self, symbol: str, run_date: Optional[str] = None) -> FilterResult:
        """Evaluates a single stock using set-based evaluation."""
        res = self.evaluate_universe(symbols=[symbol], run_date=run_date)
        if symbol in res:
            return res[symbol]

        return FilterResult(
            symbol=symbol,
            passed=False,
            metrics={"is_ath_price": False, "is_emerging_candidate": False},
            reason="No price history found in database."
        )
