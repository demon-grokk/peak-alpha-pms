"""
Centralized System Constants for Peak Alpha PMS.
Maintains all threshold parameters, data source URLs, batch sizes, and schedule constants.
"""
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXPORTS_DIR = DATA_DIR / "exports"
LOGS_DIR = DATA_DIR / "logs"
LOG_FILE_PATH = LOGS_DIR / "pms.log"
DB_PATH = DATA_DIR / "pms.db"

# Market Trading Schedule & Freshness Constants
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 10

# Ingestion Batch Sizes & Overlap Settings
PRICE_BATCH_SIZE = 50
SCANX_BATCH_SIZE = 50
CORPORATE_ACTION_OVERLAP_DAYS = 20    # Trading days overlap window during incremental fetches
CORPORATE_ACTION_REL_TOL = 0.0008      # 0.08% absolute deviation floor (above 0.03% noise, below 0.15%+ dividends)
UNIFORMITY_REL_TOL = 0.0010            # 0.10% relative tolerance across overlap days for classification

# Data Source Endpoint URLs & Tickers
NIFTY_TOTAL_MARKET_CSV_URL = (
    "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"
)
BSE_500_TICKER = "BSE-500.BO"
SCANX_API_URL = "https://scanx.dhan.co/scanx/fundamental"
SCANX_CACHE_MAX_AGE_DAYS = 60

# Strategy & Quantitative Filter Thresholds
MIN_HISTORY_TRADING_DAYS = 252        # 1 Calendar Year listing history
PRIMARY_ATH_MIN_TRADING_DAYS = 756    # 3 Calendar Years listing history
EMERGING_ATH_MIN_TRADING_DAYS = 252   # 1 Year lower bound
EMERGING_ATH_MAX_TRADING_DAYS = 755   # < 3 Years upper bound
RELATIVE_STRENGTH_TRADING_DAYS = 252  # 52-week lookback window

# Fundamental Filter Thresholds
TTM_PAT_MAX_QUARTER_GAP_DAYS = 115   # Max allowable days between quarterly reporting dates

# Floating Point Numerical Safety Epsilon
EPSILON = 1e-6

# Financial Sector Keywords for Tagging Banks, NBFCs, Insurance
FINANCIAL_SECTOR_KEYWORDS = [
    "financial services",
    "banks",
    "bank",
    "finance",
    "housing finance",
    "insurance",
    "financial",
]

# Layer 3 Allocation & Stop-Loss Engine Constants
N_TARGET = 15                     # Target portfolio slot count
TOTAL_PORTFOLIO_RISK_PCT = 18.0   # 18.0% Total Portfolio Risk Cap
TRT_PCT = 0.012                   # 1.2% Risk Target per trade (Total Portfolio Risk / N_TARGET)
MAX_ALLOC_PCT = 0.15              # 15.0% Maximum Allocation Cap per trade
MAX_RISK_GATE_PCT = 8.0           # 8.0% Hard Gate Stop Distance
ALLOC_LOOKBACK_DAYS = 60          # 60 trading days search horizon for ATR & ZigZag


