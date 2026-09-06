"""
Unit tests for Task 4 Two-Clock Scheduling Architecture & Runners.
"""
import pytest
from src.runners.price_pipeline import PricePipelineRunner
from src.runners.fundamentals_pipeline import FundamentalsPipelineRunner
from src.core.screener import ScreenerEngine

def test_price_pipeline_runner():
    runner = PricePipelineRunner()
    res = runner.run(symbols=["RELIANCE", "TCS"], fetch_latest_prices=False)
    assert isinstance(res, dict)
    assert res["total_evaluated"] == 2
    assert "ath_passed" in res

def test_fundamentals_pipeline_runner():
    runner = FundamentalsPipelineRunner()
    res = runner.run(symbols=["RELIANCE", "TCS"], fetch_latest_fundamentals=False)
    assert isinstance(res, dict)
    assert res["total_evaluated"] == 2
    assert "profit_passed" in res

def test_screener_engine():
    engine = ScreenerEngine()
    res = engine.run_full_screener(symbols=["RELIANCE", "TCS"], fetch_data=False)
    assert isinstance(res, dict)
    assert "primary_count" in res
    assert "emerging_count" in res
    assert res["primary_export_path"].endswith("primary_qualifying_stocks.csv")
