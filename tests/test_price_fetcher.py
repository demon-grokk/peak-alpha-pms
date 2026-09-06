"""
Unit tests for price fetcher freshness rules (Fix 1), incremental logic (Fix 2a),
and retroactive corporate action scale-shift detection with auto-correction.
"""
import datetime
import pandas as pd
import pytest
from src.ingestion.price_fetcher import (
    get_fetch_upper_bound,
    is_stock_price_cached,
    evaluate_overlap_window,
    detect_corporate_action_mismatch,
    log_corporate_action_event,
)
from src.db.database import db_manager
from src.db.schema import init_db

@pytest.fixture(autouse=True)
def ensure_db_schema():
    init_db()

def test_floating_point_noise_suppression():
    # Noise Suppression Test: ratios [1.000298] * 20 (0.0298% ADANIENT noise)
    ratios = [1.000298] * 20
    should_refetch, status = evaluate_overlap_window("ADANIENT", ratios)
    assert should_refetch is False
    assert status == "UNCHANGED"

def test_small_dividend_catch():
    # Small Dividend Catch Test: ratios [0.9980] * 20 (0.20% small dividend shift)
    ratios = [0.9980] * 20
    should_refetch, status = evaluate_overlap_window("TITAN", ratios)
    assert should_refetch is True
    assert status == "REFETCH_TRIGGERED"

def test_real_corporate_action_catch():
    # Standard Dividend & Stock Split Catch Test: [0.9899] * 20 (1.01% EICHERMOT dividend) and [0.5000] * 20 (2:1 split)
    ratios_div = [0.9899] * 20
    should_refetch_div, status_div = evaluate_overlap_window("EICHERMOT", ratios_div)
    assert should_refetch_div is True
    assert status_div == "REFETCH_TRIGGERED"

    ratios_split = [0.5000] * 20
    should_refetch_split, status_split = evaluate_overlap_window("ADANIENT", ratios_split)
    assert should_refetch_split is True
    assert status_split == "REFETCH_TRIGGERED"

def test_non_uniform_anomaly_classification():
    # Non-Uniform Anomaly Test: ratios ranging from 0.9800 to 0.9950 (>0.1% internal spread)
    ratios = [0.9800, 0.9950, 0.9850, 0.9900, 0.9820] * 4
    should_refetch, status = evaluate_overlap_window("ANOMALY_STOCK", ratios)
    assert should_refetch is True
    assert status == "REFETCH_TRIGGERED_NON_UNIFORM"

def test_fetch_upper_bound_weekday_before_market_close():
    # Tuesday at 14:00 (2:00 PM IST) -> upper bound should be Monday (yesterday)
    dt = datetime.datetime(2026, 8, 18, 14, 0) # Tuesday
    expected = datetime.date(2026, 8, 17) # Monday
    assert get_fetch_upper_bound(dt) == expected

def test_fetch_upper_bound_weekday_after_market_close():
    # Tuesday at 16:00 (4:00 PM IST) -> upper bound should be Tuesday (today)
    dt = datetime.datetime(2026, 8, 18, 16, 0) # Tuesday
    expected = datetime.date(2026, 8, 18) # Tuesday
    assert get_fetch_upper_bound(dt) == expected

def test_fetch_upper_bound_weekend():
    # Saturday at 12:00 -> upper bound should be Friday
    dt_sat = datetime.datetime(2026, 8, 22, 12, 0) # Saturday
    expected = datetime.date(2026, 8, 21) # Friday
    assert get_fetch_upper_bound(dt_sat) == expected

    # Sunday at 18:00 -> upper bound should be Friday
    dt_sun = datetime.datetime(2026, 8, 23, 18, 0) # Sunday
    assert get_fetch_upper_bound(dt_sun) == expected

def test_fetch_upper_bound_monday_morning():
    # Monday at 10:00 AM -> upper bound should be Friday
    dt_mon = datetime.datetime(2026, 8, 24, 10, 0) # Monday morning
    expected = datetime.date(2026, 8, 21) # Friday
    assert get_fetch_upper_bound(dt_mon) == expected

def test_stale_gap_triggers_fetch():
    # Mock stock in DB stored up to Wednesday, but now is Friday 4:00 PM (stale gap of 2 days)
    now = datetime.datetime(2026, 8, 21, 16, 0) # Friday 4:00 PM
    symbol = "TEST_STALE_STOCK"

    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("INSERT OR REPLACE INTO stocks (symbol, name) VALUES (?, ?);", (symbol, symbol))
        cursor.execute("INSERT OR REPLACE INTO price_history (symbol, date, close, adj_close) VALUES (?, ?, 100, 100);", (symbol, "2026-08-19")) # Wed

    # Cache check should return False (fetch needed)
    assert is_stock_price_cached(symbol, now=now) is False

    # Update DB to Friday 2026-08-21
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO price_history (symbol, date, close, adj_close) VALUES (?, ?, 105, 105);", (symbol, "2026-08-21"))

    # Cache check should now return True
    assert is_stock_price_cached(symbol, now=now) is True

    # Cleanup
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM stocks WHERE symbol = ?;", (symbol,))

def test_corporate_action_uniform_split_detection():
    symbol = "TEST_SPLIT_STOCK"
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM corporate_action_events WHERE symbol = ?;", (symbol,))
        cursor.execute("INSERT OR REPLACE INTO stocks (symbol, name) VALUES (?, ?);", (symbol, symbol))

        # Insert 5 historical dates with adj_close = 100.0 (pre-split)
        dates = ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]
        for d in dates:
            cursor.execute("INSERT INTO price_history (symbol, date, close, adj_close) VALUES (?, ?, 100.0, 100.0);", (symbol, d))

    # Mock freshly fetched series with 2:1 stock split (adj_close halved to 50.0 uniformly)
    fresh_dates = pd.to_datetime(dates)
    fresh_series = pd.Series([50.0] * 5, index=fresh_dates)

    should_refetch, status, ratio, count = detect_corporate_action_mismatch(symbol, fresh_series)

    assert should_refetch is True
    assert status == "REFETCH_TRIGGERED"
    assert pytest.approx(ratio, rel=1e-3) == 0.5
    assert count == 5

    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM corporate_action_events WHERE symbol = ?;", (symbol,))
        event = cursor.fetchone()

    assert event is not None
    assert event["symbol"] == symbol
    assert event["classification"] == "REFETCH_TRIGGERED"
    assert pytest.approx(event["detected_ratio"], rel=1e-3) == 0.5

    # Cleanup
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM corporate_action_events WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM stocks WHERE symbol = ?;", (symbol,))

def test_corporate_action_non_uniform_revision_detection():
    symbol = "TEST_REVISION_STOCK"
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM corporate_action_events WHERE symbol = ?;", (symbol,))
        cursor.execute("INSERT OR REPLACE INTO stocks (symbol, name) VALUES (?, ?);", (symbol, symbol))

        dates = ["2026-08-01", "2026-08-02", "2026-08-03"]
        for d in dates:
            cursor.execute("INSERT INTO price_history (symbol, date, close, adj_close) VALUES (?, ?, 100.0, 100.0);", (symbol, d))

    # Mock non-uniform adjustment (ratios: 0.5, 0.8, 0.9)
    fresh_dates = pd.to_datetime(dates)
    fresh_series = pd.Series([50.0, 80.0, 90.0], index=fresh_dates)

    should_refetch, status, ratio, count = detect_corporate_action_mismatch(symbol, fresh_series)

    assert should_refetch is True
    assert status == "REFETCH_TRIGGERED_NON_UNIFORM"

    # Cleanup
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM corporate_action_events WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM stocks WHERE symbol = ?;", (symbol,))

def test_no_mismatch_normal_incremental():
    symbol = "TEST_NORMAL_STOCK"
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("INSERT OR REPLACE INTO stocks (symbol, name) VALUES (?, ?);", (symbol, symbol))

        dates = ["2026-08-01", "2026-08-02"]
        for d in dates:
            cursor.execute("INSERT INTO price_history (symbol, date, close, adj_close) VALUES (?, ?, 100.0, 100.0);", (symbol, d))

    # Mock fresh series matching stored values (within 0.1% tolerance)
    fresh_dates = pd.to_datetime(dates)
    fresh_series = pd.Series([100.0001, 99.9999], index=fresh_dates)

    should_refetch, status, ratio, count = detect_corporate_action_mismatch(symbol, fresh_series)

    assert should_refetch is False
    assert status == "UNCHANGED"

    # Cleanup
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM stocks WHERE symbol = ?;", (symbol,))

def test_adanient_exact_micro_deviation_suppression():
    # Simulates the exact DB state for ADANIENT where stored adj_close is 3109.07 and fresh is 3110.00 (0.0298% deviation)
    symbol = "TEST_ADANIENT_SIM"
    dates = [f"2026-08-{i:02d}" for i in range(1, 21)]
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM stocks WHERE symbol = ?;", (symbol,))
        cursor.execute("INSERT INTO stocks (symbol, name) VALUES (?, ?);", (symbol, symbol))
        for d in dates:
            cursor.execute("INSERT INTO price_history (symbol, date, close, adj_close) VALUES (?, ?, 3109.07, 3109.07);", (symbol, d))

    fresh_dates = pd.to_datetime(dates)
    fresh_series = pd.Series([3110.00] * 20, index=fresh_dates)

    should_refetch, status, ratio, count = detect_corporate_action_mismatch(symbol, fresh_series)

    assert should_refetch is False
    assert status == "UNCHANGED"
    assert pytest.approx(ratio, rel=1e-4) == 1.000299

    # Cleanup
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_history WHERE symbol = ?;", (symbol,))
        cursor.execute("DELETE FROM stocks WHERE symbol = ?;", (symbol,))
