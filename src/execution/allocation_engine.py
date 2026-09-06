"""
Layer 3 Allocation & Stop-Loss Engine for Peak Alpha PMS.

Calculates point-in-time Stop Loss (ATR-filtered ZigZag + 60-day average ATR floor),
Hard 8.0% Risk Gate, Integer-floored Capital Allocation, Available Cash Check,
and persists evaluation audit records into SQLite table `entry_evaluations`.
"""
import math
import pandas as pd
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

from config.constants import (
    TRT_PCT,
    MAX_ALLOC_PCT,
    MAX_RISK_GATE_PCT,
    N_TARGET,
    ALLOC_LOOKBACK_DAYS,
)
from src.db.database import db_manager
from src.utils.logger import logger

@dataclass
class AllocationResult:
    symbol: str
    selection_reason: str
    entry_price: float
    atr14_avg_60d: float
    atr14_current: float
    swing_low_price: Optional[float]
    swing_low_date: Optional[str]
    used_fallback: int
    s_raw: float
    s_atr: float
    sl_cand: float
    r_pct: float
    capital_c: float
    available_cash: float
    trt_pct: float
    n_target: int
    max_alloc_pct: float
    raw_shares_risk: int
    raw_shares_cap: int
    allocated_shares: int
    allocated_capital: float
    allocation_pct: float
    actual_risk_pct: float
    sizing_constraint_used: str
    gate_decision: str
    was_entered: int = 0
    notes: Optional[str] = None

class AllocationEngine:
    def __init__(
        self,
        trt_pct: float = TRT_PCT,
        max_alloc_pct: float = MAX_ALLOC_PCT,
        max_risk_gate_pct: float = MAX_RISK_GATE_PCT,
        n_target: int = N_TARGET,
        lookback_days: int = ALLOC_LOOKBACK_DAYS,
    ):
        self.trt_pct = trt_pct
        self.max_alloc_pct = max_alloc_pct
        self.max_risk_gate_pct = max_risk_gate_pct
        self.n_target = n_target
        self.lookback_days = lookback_days

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calculates 14-period True Range and rolling SMA ATR on adjusted price series.
        Expects columns: 'adj_close', 'adj_high', 'adj_low'.
        """
        high = df['adj_high']
        low = df['adj_low']
        close = df['adj_close']
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        # For the first row where prev_close is NaN
        tr.iloc[0] = high.iloc[0] - low.iloc[0]

        atr = tr.rolling(window=period, min_periods=1).mean()
        return tr, atr

    def compute_zigzag_pivot_lows(
        self, df_lookback: pd.DataFrame, atr_series: pd.Series
    ) -> List[Dict[str, Any]]:
        """
        Traverses price history chronologically using a ZigZag state machine.
        Confirms a pivot low when subsequent rally >= 1.0 * ATR14(t).
        """
        confirmed_lows = []
        state = "seeking_low"

        if df_lookback.empty:
            return confirmed_lows

        candidate_low = float(df_lookback.iloc[0]["adj_low"])
        candidate_low_date = str(df_lookback.iloc[0]["date"])
        candidate_low_idx = df_lookback.index[0]

        candidate_high = float(df_lookback.iloc[0]["adj_high"])
        candidate_high_date = str(df_lookback.iloc[0]["date"])
        candidate_high_idx = df_lookback.index[0]

        for idx, row in df_lookback.iterrows():
            curr_low = float(row["adj_low"])
            curr_high = float(row["adj_high"])
            curr_date = str(row["date"])
            curr_atr = float(atr_series.loc[idx])

            if state == "seeking_low":
                if curr_low < candidate_low:
                    candidate_low = curr_low
                    candidate_low_date = curr_date
                    candidate_low_idx = idx
                elif idx != candidate_low_idx and curr_atr > 0 and (curr_high - candidate_low) >= (1.0 * curr_atr):
                    # Confirm pivot low if rally >= 1.0 * ATR from candidate low on a subsequent bar
                    confirmed_lows.append({
                        "price": candidate_low,
                        "date": candidate_low_date
                    })
                    state = "seeking_high"
                    candidate_high = curr_high
                    candidate_high_date = curr_date
                    candidate_high_idx = idx

            elif state == "seeking_high":
                if curr_high > candidate_high:
                    candidate_high = curr_high
                    candidate_high_date = curr_date
                    candidate_high_idx = idx
                elif idx != candidate_high_idx and curr_atr > 0 and (candidate_high - curr_low) >= (1.0 * curr_atr):
                    state = "seeking_low"
                    candidate_low = curr_low
                    candidate_low_date = curr_date
                    candidate_low_idx = idx




        return confirmed_lows

    def evaluate_price_series(
        self,
        symbol: str,
        df: pd.DataFrame,
        capital_c: float,
        available_cash: float,
        selection_reason: str = "PASSES_ALL_FILTERS",
        notes: Optional[str] = None
    ) -> AllocationResult:
        """
        Evaluates a stock's historical price DataFrame to derive ATR floors,
        ZigZag structural floors, stop loss, risk gating, and position sizing.
        """
        if df.empty:
            raise ValueError(f"Price DataFrame for {symbol} is empty.")

        # Ensure sorted by date
        df = df.sort_values("date").reset_index(drop=True)

        # Filter out invalid, zero, null, or corrupted price rows before evaluation
        df = df[
            (df["close"].notnull()) & (df["close"] > 0) &
            (df["adj_close"].notnull()) & (df["adj_close"] > 0) &
            (df["high"].notnull()) & (df["low"].notnull()) &
            (df["high"] >= df["low"])
        ].copy().reset_index(drop=True)

        # Prepare adjusted High and Low if missing
        if "adj_high" not in df.columns:
            if "high" in df.columns and "close" in df.columns and "adj_close" in df.columns:
                scale = df["adj_close"] / df["close"]
                df["adj_high"] = df["high"] * scale
                df["adj_low"] = df["low"] * scale
            else:
                df["adj_high"] = df["close"]
                df["adj_low"] = df["close"]


        # 1. Compute ATR series
        tr_series, atr_series = self.calculate_atr(df, period=14)

        # 2. Extract latest bar (Entry Price)
        latest_idx = len(df) - 1
        entry_price = float(df.iloc[latest_idx]["adj_close"])
        atr14_current = float(atr_series.iloc[latest_idx])

        # 3. Compute Volatility Floor (S_atr) over last 60 trading days
        lookback_start_idx = max(0, len(df) - self.lookback_days)
        atr_60d = atr_series.iloc[lookback_start_idx:]
        atr14_avg_60d = float(atr_60d.mean())

        s_atr = entry_price - (2.0 * atr14_avg_60d)

        # 4. Compute Structural Floor via ZigZag (S_raw)
        df_60d = df.iloc[lookback_start_idx:]
        confirmed_lows = self.compute_zigzag_pivot_lows(df_60d, atr_series.iloc[lookback_start_idx:])

        if confirmed_lows:
            most_recent_low = confirmed_lows[-1]
            swing_low_price = float(most_recent_low["price"])
            swing_low_date = str(most_recent_low["date"])
            s_raw = swing_low_price - (0.25 * atr14_current)
            used_fallback = 0
        else:
            swing_low_price = None
            swing_low_date = None
            s_raw = s_atr
            used_fallback = 1

        # 5. Determine Candidate Stop Loss (SL_cand)
        sl_cand = min(s_raw, s_atr)
        r_pct = ((entry_price - sl_cand) / entry_price) * 100.0 if entry_price > 0 else 0.0

        # 6. Sizing & Gate Evaluation
        raw_shares_risk = math.floor((capital_c * self.trt_pct) / (entry_price - sl_cand)) if entry_price > sl_cand else 0
        raw_shares_cap = math.floor((capital_c * self.max_alloc_pct) / entry_price) if entry_price > 0 else 0

        if r_pct > self.max_risk_gate_pct:
            gate_decision = "REJECTED_RISK_GT_8PCT"
            allocated_shares = 0
            allocated_capital = 0.0
            allocation_pct = 0.0
            actual_risk_pct = 0.0
            sizing_constraint_used = "RISK_BASED"
        else:
            if raw_shares_risk <= raw_shares_cap:
                allocated_shares = raw_shares_risk
                sizing_constraint_used = "RISK_BASED"
            else:
                allocated_shares = raw_shares_cap
                sizing_constraint_used = "MAX_ALLOC_CAPPED"

            allocated_capital = allocated_shares * entry_price
            allocation_pct = (allocated_capital / capital_c) * 100.0 if capital_c > 0 else 0.0
            actual_risk_pct = (allocated_capital * (r_pct / 100.0)) / capital_c * 100.0 if capital_c > 0 else 0.0

            if available_cash < allocated_capital:
                gate_decision = "INSUFFICIENT_CASH"
            else:
                gate_decision = "APPROVED"

        return AllocationResult(
            symbol=symbol,
            selection_reason=selection_reason,
            entry_price=entry_price,
            atr14_avg_60d=atr14_avg_60d,
            atr14_current=atr14_current,
            swing_low_price=swing_low_price,
            swing_low_date=swing_low_date,
            used_fallback=used_fallback,
            s_raw=s_raw,
            s_atr=s_atr,
            sl_cand=sl_cand,
            r_pct=r_pct,
            capital_c=capital_c,
            available_cash=available_cash,
            trt_pct=self.trt_pct,
            n_target=self.n_target,
            max_alloc_pct=self.max_alloc_pct,
            raw_shares_risk=raw_shares_risk,
            raw_shares_cap=raw_shares_cap,
            allocated_shares=allocated_shares,
            allocated_capital=allocated_capital,
            allocation_pct=allocation_pct,
            actual_risk_pct=actual_risk_pct,
            sizing_constraint_used=sizing_constraint_used,
            gate_decision=gate_decision,
            was_entered=0,
            notes=notes
        )

    def evaluate_symbol_from_db(
        self,
        symbol: str,
        capital_c: float,
        available_cash: float,
        selection_reason: str = "PASSES_ALL_FILTERS",
        notes: Optional[str] = None
    ) -> AllocationResult:
        # Check for ath_baseline_start_date and unresolved price anomalies / quarantine
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ath_baseline_start_date, listing_date FROM stocks WHERE symbol = ?;", (symbol,))
            stock_row = cursor.fetchone()
            baseline_date = stock_row["ath_baseline_start_date"] if stock_row else None
            effective_start_date = baseline_date or (stock_row["listing_date"] if stock_row else None) or "1900-01-01"

            cursor.execute("""
                SELECT classification FROM corporate_action_events
                WHERE symbol = ? AND classification IN ('NON_UNIFORM_REVISION', 'DATA_CORRUPTION', 'UNRESOLVED_ANOMALY', 'STRUCTURAL_DEMERGER_EVENT')
                ORDER BY id DESC LIMIT 1;
            """, (symbol,))
            anomaly_row = cursor.fetchone()

        # If flagged for structural anomaly/demerger but baseline_date is NOT set yet -> Quarantine
        if anomaly_row and not baseline_date:
            logger.warning(f"[{symbol}] Quarantined due to structural anomaly / demerger ({anomaly_row['classification']}). Awaiting manual ath_baseline_start_date setting.")
            return self.evaluate_price_series(
                symbol=symbol,
                df=pd.DataFrame(),
                capital_c=capital_c,
                available_cash=available_cash,
                selection_reason=selection_reason,
                notes=f"QUARANTINED_ANOMALY_{anomaly_row['classification']}_NEEDS_BASELINE"
            )

        sql = """
            SELECT p.date, p.open, p.high, p.low, p.close, p.adj_close
            FROM price_history p
            WHERE p.symbol = ? 
              AND p.date >= ?
              AND p.close IS NOT NULL AND p.close > 0
              AND p.adj_close IS NOT NULL AND p.adj_close > 0
              AND p.high IS NOT NULL AND p.low IS NOT NULL
              AND p.high >= p.low
            ORDER BY p.date ASC;
        """

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (symbol, effective_start_date))
            rows = cursor.fetchall()

        if not rows:
            raise ValueError(f"No price history found in DB for symbol '{symbol}'.")

        df = pd.DataFrame([dict(r) for r in rows])
        return self.evaluate_price_series(
            symbol=symbol,
            df=df,
            capital_c=capital_c,
            available_cash=available_cash,
            selection_reason=selection_reason,
            notes=notes
        )

    def save_evaluation(self, result: AllocationResult) -> int:
        """
        Persists allocation evaluation result into SQLite `entry_evaluations` table.
        """
        sql = """
        INSERT INTO entry_evaluations (
            symbol, selection_reason, entry_price, atr14_avg_60d, atr14_current,
            swing_low_price, swing_low_date, used_fallback, s_raw, s_atr, sl_cand, r_pct,
            capital_c, available_cash, trt_pct, n_target, max_alloc_pct,
            raw_shares_risk, raw_shares_cap, allocated_shares, allocated_capital,
            allocation_pct, actual_risk_pct, sizing_constraint_used, gate_decision,
            was_entered, notes
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        );
        """
        data = (
            result.symbol,
            result.selection_reason,
            result.entry_price,
            result.atr14_avg_60d,
            result.atr14_current,
            result.swing_low_price,
            result.swing_low_date,
            result.used_fallback,
            result.s_raw,
            result.s_atr,
            result.sl_cand,
            result.r_pct,
            result.capital_c,
            result.available_cash,
            result.trt_pct,
            result.n_target,
            result.max_alloc_pct,
            result.raw_shares_risk,
            result.raw_shares_cap,
            result.allocated_shares,
            result.allocated_capital,
            result.allocation_pct,
            result.actual_risk_pct,
            result.sizing_constraint_used,
            result.gate_decision,
            result.was_entered,
            result.notes
        )
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, data)
            eval_id = cursor.lastrowid

        logger.info(
            f"Saved entry evaluation #{eval_id} for {result.symbol}: "
            f"Gate={result.gate_decision}, Shares={result.allocated_shares}, "
            f"Capital=₹{result.allocated_capital:,.2f}, Stop={result.r_pct:.2f}%"
        )
        return eval_id
