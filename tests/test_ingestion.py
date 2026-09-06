"""
Unit tests for Task 1 data ingestion and schema setup.
"""
import pytest
from src.db.database import db_manager
from src.db.schema import init_db
from src.utils.helpers import format_ticker_nse, is_financial_sector, is_ath_with_epsilon

def test_helpers():
    assert format_ticker_nse("RELIANCE") == "RELIANCE.NS"
    assert format_ticker_nse("TCS.NS") == "TCS.NS"
    assert format_ticker_nse("^BSE500") == "^BSE500"
    
    assert is_financial_sector("Financial Services") is True
    assert is_financial_sector("Banks") is True
    assert is_financial_sector("Capital Goods") is False
    
    assert is_ath_with_epsilon(100.0, 100.0) is True
    assert is_ath_with_epsilon(99.999999, 100.0) is True
    assert is_ath_with_epsilon(95.0, 100.0) is False

def test_db_schema():
    init_db()
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row["name"] for row in cursor.fetchall()]
        
    expected = [
        "stocks", "price_history", "benchmark_history",
        "fundamentals_quarterly", "fundamentals_ttm", "screen_results",
        "governance_flags", "portfolio_positions"
    ]
    for table in expected:
        assert table in tables
