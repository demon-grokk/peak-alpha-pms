"""
Unit tests for quarterly fundamentals ingestion, Filter 2 (ATH TTM Profit), and ScanX integration.
"""
import pytest
from src.filters.ath_profit import ATHProfitFilter
from src.core.manual_review import ManualReviewManager
from src.ingestion.scanx_fundamentals import fetch_and_store_scanx_fundamentals, yyyymm_to_iso_date
from src.filters.base import FilterResult

def test_yyyymm_date_conversion():
    assert yyyymm_to_iso_date("202606") == "2026-06-30"
    assert yyyymm_to_iso_date("202603") == "2026-03-31"
    assert yyyymm_to_iso_date("202512") == "2025-12-31"
    assert yyyymm_to_iso_date("202509") == "2025-09-30"

from src.db.database import db_manager

def test_scanx_fundamentals_ingestion():
    count = fetch_and_store_scanx_fundamentals(["RELIANCE", "TCS"])
    if count == 0:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT review_outcome FROM fundamentals_ttm WHERE symbol IN ('RELIANCE', 'TCS')")
            rows = cursor.fetchall()
            if any("Token Expired" in (r[0] or "") or "HTTP" in (r[0] or "") for r in rows):
                pytest.skip("ScanX API token is expired or unauthorized in .env - skipped live API fetch verification")
    assert count >= 1

def test_ath_profit_filter_structure():
    filter2 = ATHProfitFilter()
    result = filter2.evaluate("TCS")
    assert isinstance(result, FilterResult)
    assert result.symbol == "TCS"
    assert "current_ttm_pat" in result.metrics
    assert "needs_manual_review" in result.metrics

def test_manual_review_manager_export():
    path = ManualReviewManager.export_screener_watchlist()
    assert path.endswith("screener_manual_review_watchlist.csv")
