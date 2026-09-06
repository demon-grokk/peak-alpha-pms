"""
Helper functions for Peak Alpha PMS.
"""
from typing import Optional
from config.constants import EPSILON, FINANCIAL_SECTOR_KEYWORDS

def format_ticker_nse(symbol: str) -> str:
    """Converts raw symbol (e.g., RELIANCE) to yfinance NSE ticker format (RELIANCE.NS)."""
    symbol = symbol.strip().upper()
    if symbol.endswith(".NS") or symbol.endswith(".BO") or symbol.startswith("^"):
        return symbol
    return f"{symbol}.NS"

def is_financial_sector(industry: str) -> bool:
    """Determines if an industry string matches financial sector keywords."""
    if not industry:
        return False
    ind_lower = industry.strip().lower()
    return any(keyword in ind_lower for keyword in FINANCIAL_SECTOR_KEYWORDS)

def is_ath_with_epsilon(current_val: float, max_historical_val: float, epsilon: float = EPSILON) -> bool:
    """
    Checks if current value is at or above historical max with numerical safety epsilon.
    Using current_val >= (max_historical_val - epsilon) prevents float rounding misses.
    """
    if current_val is None or max_historical_val is None:
        return False
    return float(current_val) >= (float(max_historical_val) - epsilon)

def calculate_52w_return(current_price: float, price_52w_ago: float) -> Optional[float]:
    """Calculates simple return over 52 weeks (252 trading days)."""
    if current_price is None or price_52w_ago is None or price_52w_ago <= 0:
        return None
    return (float(current_price) - float(price_52w_ago)) / float(price_52w_ago)
