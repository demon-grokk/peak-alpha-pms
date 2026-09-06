"""
Base filter interface for Peak Alpha PMS screening strategies.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class FilterResult:
    def __init__(self, symbol: str, passed: bool, metrics: Dict[str, Any], reason: str = ""):
        self.symbol = symbol
        self.passed = passed
        self.metrics = metrics
        self.reason = reason

    def __repr__(self):
        return f"<FilterResult symbol={self.symbol} passed={self.passed} reason={self.reason}>"

class BaseFilter(ABC):
    @abstractmethod
    def evaluate(self, symbol: str, run_date: Optional[str] = None) -> FilterResult:
        """Evaluates a stock symbol against filter rules as of run_date."""
        pass
