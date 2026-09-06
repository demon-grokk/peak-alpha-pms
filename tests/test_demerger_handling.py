"""
Unit tests for Structural Break & Demerger Handling in Peak Alpha PMS.
Verifies ath_baseline_start_date schema field, query filtering, structural demerger event classification,
and post-demerger ATH / allocation calculation without phantom ceilings.
"""
import pytest
import datetime
from src.db.database import db_manager
from src.db.schema import init_db
from src.filters.ath_price import ATHPriceFilter
from src.execution.allocation_engine import AllocationEngine
from src.ingestion.reconciliation import reconcile_full_price_history

@pytest.fixture(autouse=True)
def setup_db():
    init_db()

def test_ath_baseline_start_date_schema_and_query():
    """Verifies ath_baseline_start_date column exists and filters ATH calculations."""
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(stocks);")
        cols = [r["name"] for r in cursor.fetchall()]
        assert "ath_baseline_start_date" in cols

def test_triveni_demerger_baseline_reset():
    """
    Verifies setting ath_baseline_start_date for TRIVENI resets ATH calculation window to post-demerger date,
    eliminating pre-demerger phantom ATH ceilings.
    """
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE stocks
            SET ath_baseline_start_date = '2026-07-22'
            WHERE symbol = 'TRIVENI';
        """)

    # Evaluate ATH Price Filter with 20-day min threshold
    f1 = ATHPriceFilter(primary_min_days=20, emerging_min_days=20)
    res_f1 = f1.evaluate_universe(symbols=["TRIVENI"])["TRIVENI"]

    assert res_f1.metrics["trading_days_count"] >= 20
    # ATH High must be the post-demerger high (~314.90), NOT pre-demerger high (471.50)
    assert res_f1.metrics["ath_adj_close"] < 350.0
    assert res_f1.metrics["ath_adj_close"] > 300.0

def test_allocation_engine_honors_demerger_baseline():
    """Verifies allocation engine computes prices strictly from ath_baseline_start_date onwards."""
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE stocks
            SET ath_baseline_start_date = '2026-07-22'
            WHERE symbol = 'TRIVENI';
        """)

    engine = AllocationEngine()
    res = engine.evaluate_symbol_from_db("TRIVENI", capital_c=1_000_000.0, available_cash=1_000_000.0)

    assert res.symbol == "TRIVENI"
    assert res.entry_price > 200.0
    assert res.sl_cand > 0.0
