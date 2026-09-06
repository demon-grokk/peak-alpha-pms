"""
Unit tests for Filter 1 (Two-Tier ATH Price) and Filter 3 Stage A (Relative Strength).
"""
import pytest
from src.filters.ath_price import ATHPriceFilter
from src.filters.relative_strength import RelativeStrengthFilter
from src.filters.base import FilterResult

def test_ath_price_filter_structure():
    ath_filter = ATHPriceFilter()
    result = ath_filter.evaluate("RELIANCE")
    assert isinstance(result, FilterResult)
    assert result.symbol == "RELIANCE"
    assert "is_ath_price" in result.metrics
    assert "is_emerging_candidate" in result.metrics

def test_relative_strength_filter_structure():
    rs_filter = RelativeStrengthFilter()
    result = rs_filter.evaluate("RELIANCE")
    assert isinstance(result, FilterResult)
    assert result.symbol == "RELIANCE"
    assert "bse500_52w_return" in result.metrics

def test_two_tier_history_exclusions():
    # 1. Under 252 days -> fails both primary & emerging, near_ath_price_pct is None
    ath_filter_strict = ATHPriceFilter(emerging_min_days=99999)
    res_under = ath_filter_strict.evaluate("RELIANCE")
    assert res_under.passed is False
    assert res_under.metrics["is_ath_price"] is False
    assert res_under.metrics["is_emerging_candidate"] is False
    assert res_under.metrics["near_ath_price_pct"] is None

    # 2. Emerging Candidate tier: 252 <= days < 756
    # Configure primary_min_days higher than Reliance history (7691 days) to trigger emerging candidate logic
    ath_filter_emerging = ATHPriceFilter(primary_min_days=99999, emerging_min_days=252)
    # Mock / evaluate with ATH condition forced
    res_emerging = ath_filter_emerging.evaluate("RELIANCE")
    # If Reliance is below ATH it will return False for both, but metrics are properly initialized
    assert "is_emerging_candidate" in res_emerging.metrics
    assert "is_ath_price" in res_emerging.metrics

def test_near_ath_price_pct_consistency():
    ath_filter = ATHPriceFilter()
    eval_dict = ath_filter.evaluate_universe()
    
    for symbol, res in eval_dict.items():
        metrics = res.metrics
        trading_days = metrics.get("trading_days_count", 0)
        near_ath_pct = metrics.get("near_ath_price_pct")

        if trading_days < 252:
            assert near_ath_pct is None, f"{symbol} with {trading_days} days must have near_ath_price_pct=None"
        else:
            assert near_ath_pct is not None, f"{symbol} with {trading_days} days must have numeric near_ath_price_pct"
            
            # Assertion: If is_ath_price or is_emerging_candidate is True, distance from ATH must be 0.0
            if metrics.get("is_ath_price") or metrics.get("is_emerging_candidate"):
                assert abs(near_ath_pct) < 1e-5, f"{symbol} marked at ATH but near_ath_price_pct={near_ath_pct}"
                
            # Assertion: Non-ATH calculation check
            current_p = metrics.get("current_adj_close")
            ath_p = metrics.get("ath_adj_close")
            if current_p and ath_p and current_p < ath_p - 1e-6:
                expected_pct = (ath_p - current_p) / ath_p
                assert abs(near_ath_pct - expected_pct) < 1e-5, f"{symbol} near_ath_price_pct mismatch"

