"""
Unit and Integration Tests for Layer 3 Allocation & Stop-Loss Engine.
"""
import pytest
import pandas as pd
import numpy as np

from src.execution.allocation_engine import AllocationEngine, AllocationResult
from src.db.schema import init_db
from src.db.database import db_manager

@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()

def create_synthetic_df(prices: list[float], atr_val: float = 2.0) -> pd.DataFrame:
    """Helper to generate a synthetic daily price DataFrame for testing."""
    records = []
    for i, p in enumerate(prices):
        date_str = f"2026-01-{(i%28)+1:02d}"
        records.append({
            "symbol": "TEST",
            "date": f"2026-01-{i+1:03d}",
            "open": p,
            "high": p + (atr_val / 2.0),
            "low": p - (atr_val / 2.0),
            "close": p,
            "adj_close": p,
            "adj_high": p + (atr_val / 2.0),
            "adj_low": p - (atr_val / 2.0),
            "volume": 10000
        })
    return pd.DataFrame(records)

def test_tight_base_sizing():
    """
    Test 1: Tight Base Sizing Test.
    Verify setup with 4.0% stop gets capped at 15.0% allocation,
    sets sizing_constraint_used = 'MAX_ALLOC_CAPPED', and asserts actual_risk_pct == 0.6%.
    """
    engine = AllocationEngine(
        trt_pct=0.012,
        max_alloc_pct=0.15,
        max_risk_gate_pct=8.0
    )

    # 65 days of steady prices around 100, where ATR floor yields SL = 96.0 (4% risk)
    # Entry price = 100.0, SL_cand = 96.0
    # avg(ATR14) = 2.0 -> S_atr = 100 - 2.0*2.0 = 96.0
    prices = [100.0] * 70
    df = create_synthetic_df(prices, atr_val=2.0)

    res = engine.evaluate_price_series(
        symbol="TEST_TIGHT",
        df=df,
        capital_c=1_000_000.0,
        available_cash=1_000_000.0
    )

    assert res.entry_price == 100.0
    assert abs(res.sl_cand - 96.0) < 1e-4
    assert abs(res.r_pct - 4.0) < 1e-4
    assert res.raw_shares_risk == 3000  # floor((1M * 0.012) / 4) = 3000
    assert res.raw_shares_cap == 1500   # floor((1M * 0.15) / 100) = 1500
    assert res.allocated_shares == 1500
    assert res.sizing_constraint_used == "MAX_ALLOC_CAPPED"
    assert abs(res.allocated_capital - 150000.0) < 1e-4
    assert abs(res.allocation_pct - 15.0) < 1e-4
    assert abs(res.actual_risk_pct - 0.6) < 1e-4
    assert res.gate_decision == "APPROVED"

def test_hard_gate_rejection():
    """
    Test 2: Hard Gate Rejection Test.
    Verify setup with 9.5% stop gets marked REJECTED_RISK_GT_8PCT with 0 shares allocated.
    """
    engine = AllocationEngine(
        trt_pct=0.012,
        max_alloc_pct=0.15,
        max_risk_gate_pct=8.0
    )

    # Entry = 100.0, avg(ATR) = 4.75 -> S_atr = 100 - 2*4.75 = 90.50 (9.5% risk)
    prices = [100.0] * 70
    df = create_synthetic_df(prices, atr_val=4.75)

    res = engine.evaluate_price_series(
        symbol="TEST_REJECT",
        df=df,
        capital_c=1_000_000.0,
        available_cash=1_000_000.0
    )

    assert res.entry_price == 100.0
    assert abs(res.r_pct - 9.5) < 1e-4
    assert res.gate_decision == "REJECTED_RISK_GT_8PCT"
    assert res.allocated_shares == 0
    assert res.allocated_capital == 0.0
    assert res.allocation_pct == 0.0
    assert res.actual_risk_pct == 0.0

def test_cash_starvation():
    """
    Test 3: Cash Starvation Test.
    Provide available_cash = 50,000 for a trade requiring ₹1,50,000.
    Verify status is INSUFFICIENT_CASH.
    """
    engine = AllocationEngine(
        trt_pct=0.012,
        max_alloc_pct=0.15,
        max_risk_gate_pct=8.0
    )

    prices = [100.0] * 70
    df = create_synthetic_df(prices, atr_val=2.0)

    res = engine.evaluate_price_series(
        symbol="TEST_STARVE",
        df=df,
        capital_c=1_000_000.0,
        available_cash=50_000.0  # Required capital = ₹150,000 > ₹50,000
    )

    assert res.allocated_shares == 1500
    assert res.allocated_capital == 150000.0
    assert res.gate_decision == "INSUFFICIENT_CASH"

def test_zigzag_state_machine():
    """
    Test 4: ZigZag State Machine Test.
    Verify synthetic price series with multiple pullbacks correctly selects
    the most recent confirmed low rather than an older 60-day absolute minimum.
    """
    engine = AllocationEngine(lookback_days=60)

    # Generate 70 days of synthetic prices
    # Older pullback at day 10: Low = 80.0, followed by rally to 90.0
    # Recent pullback at day 45: Low = 92.0, followed by rally to 100.0
    # ATR = 2.0
    dates = [f"2026-01-{i+1:03d}" for i in range(70)]
    records = []

    for i, d in enumerate(dates):
        if i <= 10:
            price = 100.0 - (2.0 * i)  # Drops to 80.0 at i=10
        elif 10 < i <= 25:
            price = 80.0 + (1.0 * (i - 10))  # Rallies to 95.0 at i=25
        elif 25 < i <= 45:
            price = 95.0 - (0.15 * (i - 25))  # Pulls back to 92.0 at i=45
        else:
            price = 92.0 + (0.5 * (i - 45))  # Rallies to 104.5 at i=70

        records.append({
            "symbol": "TEST_ZIGZAG",
            "date": d,
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "adj_close": price,
            "adj_high": price + 1.0,
            "adj_low": price - 1.0,
            "volume": 10000
        })

    df = pd.DataFrame(records)
    tr_s, atr_s = engine.calculate_atr(df, period=14)
    df_60d = df.iloc[-60:].reset_index(drop=True)
    atr_60d = atr_s.iloc[-60:].reset_index(drop=True)

    confirmed_lows = engine.compute_zigzag_pivot_lows(df_60d, atr_60d)

    # Should have identified multiple confirmed lows, and the last confirmed low should be the recent one (~91.0-92.0), not 80.0
    assert len(confirmed_lows) >= 1
    most_recent_low = confirmed_lows[-1]
    assert most_recent_low["price"] > 85.0  # Confirms it selected recent low (~91.0) over older absolute min (80.0)

def test_fallback_parabolic_run():
    """
    Test 5: Fallback Test.
    Verify parabolic run with no pullback activates used_fallback = 1 and matches S_atr.
    """
    engine = AllocationEngine(lookback_days=60)

    # 10 days of synthetic prices where initial bar sets high ATR (14.0),
    # and subsequent 9 bars have tight daily movement (range = 0.1) rising by 0.01 per day.
    # Total rally (0.14) < 1.0 * ATR (1.49+), so zero pivot lows are confirmed.
    records = []
    records.append({
        "symbol": "TEST_PARABOLIC", "date": "2026-01-001",
        "open": 100.0, "high": 114.0, "low": 100.0, "close": 100.0,
        "adj_close": 100.0, "adj_high": 114.0, "adj_low": 100.0, "volume": 10000
    })

    for i in range(1, 10):
        p = 100.0 + (i * 0.01)
        records.append({
            "symbol": "TEST_PARABOLIC", "date": f"2026-01-{i+1:03d}",
            "open": p, "high": p + 0.05, "low": p - 0.05, "close": p,
            "adj_close": p, "adj_high": p + 0.05, "adj_low": p - 0.05, "volume": 10000
        })

    df = pd.DataFrame(records)

    res = engine.evaluate_price_series(
        symbol="TEST_PARABOLIC",
        df=df,
        capital_c=1_000_000.0,
        available_cash=1_000_000.0
    )

    assert res.used_fallback == 1
    assert res.swing_low_price is None
    assert res.swing_low_date is None
    assert abs(res.s_raw - res.s_atr) < 1e-4
    assert abs(res.sl_cand - res.s_atr) < 1e-4

def test_sl_cand_enforces_min_operator_not_max():
    """
    Test 6: Dedicated Regression Test for min(S_raw, S_atr).
    Asserts that SL_cand strictly selects min(S_raw, S_atr) and not max(),
    ensuring deep structural downside risk (10.425%) is honored and correctly gated.
    """
    engine = AllocationEngine(
        trt_pct=0.012,
        max_alloc_pct=0.15,
        max_risk_gate_pct=8.0,
        lookback_days=60
    )

    # Entry = 1000.0
    # S_atr = ~980.92 (Tight volatility floor -> 1.91% risk)
    # S_raw = 896.0 - 0.25 * 1.0 = 895.75 (Deep structural base -> 10.425% risk)
    # min(S_raw, S_atr) = min(895.75, 980.92) = 895.75 (10.425% risk)
    # Since 10.425% > 8.0%, gate decision MUST be REJECTED_RISK_GT_8PCT.
    # If max() were used, SL_cand = 980.92 (1.91% risk), which would falsely APPROVE.

    dates = [f"2026-01-{i+1:03d}" for i in range(70)]
    records = []

    # 14 initial bars with high ATR (20.0) -> high=1020, low=980
    for i in range(14):
        records.append({
            "symbol": "TEST_MIN_OP", "date": dates[i],
            "open": 1000.0, "high": 1020.0, "low": 980.0, "close": 1000.0,
            "adj_close": 1000.0, "adj_high": 1020.0, "adj_low": 980.0, "volume": 10000
        })

    # Day 15: Low drops to 896.0 (deep structural base)
    records.append({
        "symbol": "TEST_MIN_OP", "date": dates[14],
        "open": 1000.0, "high": 1000.0, "low": 896.0, "close": 1000.0,
        "adj_close": 1000.0, "adj_high": 1000.0, "adj_low": 896.0, "volume": 10000
    })

    # Day 16..69: Price rallies to 1000.0 with high=1001.0, low=1000.5 (range=0.5)
    # Confirmation rally >= 1.0 * ATR confirms 896.0 as pivot low.
    for i in range(15, 70):
        records.append({
            "symbol": "TEST_MIN_OP", "date": dates[i],
            "open": 1000.0, "high": 1001.0, "low": 1000.5, "close": 1000.0,
            "adj_close": 1000.0, "adj_high": 1001.0, "adj_low": 1000.5, "volume": 10000
        })

    df = pd.DataFrame(records)

    res = engine.evaluate_price_series(
        symbol="TEST_MIN_OP",
        df=df,
        capital_c=1_000_000.0,
        available_cash=1_000_000.0
    )

    assert res.entry_price == 1000.0
    assert abs(res.s_raw - 895.75) < 1e-4
    assert abs(res.s_atr - 980.9166666) < 1e-3
    # Explicitly assert min operator selection: 895.75, NOT 980.92
    assert res.sl_cand == res.s_raw
    assert abs(res.sl_cand - 895.75) < 1e-4
    assert abs(res.r_pct - 10.425) < 1e-3
    assert res.gate_decision == "REJECTED_RISK_GT_8PCT"
    assert res.allocated_shares == 0







