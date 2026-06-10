BENCHMARK_TICKERS = ["SPY", "QQQ", "DIA", "IWM"]

DEFAULT_HISTORY_MONTHS = 6
FORECAST_DAYS = 5

MAX_CONCURRENT_FETCHES = 3
FETCH_DELAY_SECONDS = 0.5

CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 8192

# Fallback sector valuation averages when only one ticker per sector
MARKET_DEFAULT_PE = 22.0
MARKET_DEFAULT_PB = 4.0

# Sector premium/discount threshold (±15%)
SECTOR_COMPARISON_THRESHOLD = 0.15

# ARIMA settings
ARIMA_ORDER = (5, 1, 0)
ARIMA_LOOKBACK_DAYS = 60

# Prophet settings
PROPHET_SEASONALITY_MODE = "multiplicative"

# Forecast confidence interval alpha (0.20 = 80% CI)
FORECAST_CI_ALPHA = 0.20

# Number of trailing days of price history kept for charting
CHART_HISTORY_DAYS = 90

# Weights for the rule-based bullish/bearish probability score (must sum to 1.0)
PROBABILITY_WEIGHTS = {
    "trend": 0.20,
    "rsi": 0.15,
    "macd": 0.15,
    "sma_stack": 0.15,
    "bollinger": 0.10,
    "forecast": 0.25,
}
