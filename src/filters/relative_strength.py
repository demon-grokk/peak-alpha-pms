from typing import Optional
from config.constants import RELATIVE_STRENGTH_TRADING_DAYS
from src.db.database import db_manager
from src.filters.base import BaseFilter, FilterResult
from src.utils.helpers import calculate_52w_return

class RelativeStrengthFilter(BaseFilter):
    def __init__(self, benchmark_name: str = "BSE500", lookback_days: int = RELATIVE_STRENGTH_TRADING_DAYS):
        self.benchmark_name = benchmark_name
        self.lookback_days = lookback_days

    def _get_52w_return_stock(self, symbol: str, run_date: Optional[str] = None) -> Optional[float]:
        sql = f"""
            SELECT date, adj_close
            FROM price_history
            WHERE symbol = ? AND adj_close IS NOT NULL {"AND date <= ?" if run_date else ""}
            ORDER BY date DESC
            LIMIT ?;
        """
        params = [symbol, run_date, self.lookback_days + 1] if run_date else [symbol, self.lookback_days + 1]

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        if len(rows) < self.lookback_days:
            return None

        current_price = rows[0]["adj_close"]
        price_52w_ago = rows[-1]["adj_close"]
        return calculate_52w_return(current_price, price_52w_ago)

    def _get_52w_return_benchmark(self, run_date: Optional[str] = None) -> Optional[float]:
        sql = f"""
            SELECT date, adj_close
            FROM benchmark_history
            WHERE benchmark_name = ? AND adj_close IS NOT NULL {"AND date <= ?" if run_date else ""}
            ORDER BY date DESC
            LIMIT ?;
        """
        params = [self.benchmark_name, run_date, self.lookback_days + 1] if run_date else [self.benchmark_name, self.lookback_days + 1]

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        if len(rows) < self.lookback_days:
            return None

        current_price = rows[0]["adj_close"]
        price_52w_ago = rows[-1]["adj_close"]
        return calculate_52w_return(current_price, price_52w_ago)

    def evaluate(self, symbol: str, run_date: Optional[str] = None) -> FilterResult:
        """
        Evaluates whether stock 52-week return exceeds BSE 500 benchmark 52-week return.
        """
        stock_return = self._get_52w_return_stock(symbol, run_date)
        bm_return = self._get_52w_return_benchmark(run_date)

        if stock_return is None:
            return FilterResult(
                symbol=symbol,
                passed=False,
                metrics={},
                reason=f"Insufficient stock price history to compute 52-week return (< {self.lookback_days} days)."
            )

        if bm_return is None:
            return FilterResult(
                symbol=symbol,
                passed=False,
                metrics={"stock_52w_return": stock_return},
                reason=f"Insufficient benchmark history for {self.benchmark_name} (< {self.lookback_days} days)."
            )

        passed = stock_return > bm_return

        metrics = {
            "stock_52w_return": stock_return,
            "bse500_52w_return": bm_return,
            "relative_alpha_52w": stock_return - bm_return
        }

        reason = (
            f"Outperforms BSE 500 (Stock: {stock_return*100:.2f}%, BSE500: {bm_return*100:.2f}%)"
            if passed else
            f"Underperforms BSE 500 (Stock: {stock_return*100:.2f}%, BSE500: {bm_return*100:.2f}%)"
        )

        return FilterResult(symbol=symbol, passed=passed, metrics=metrics, reason=reason)
