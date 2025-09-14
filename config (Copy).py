# MEXC API Configuration
API_KEY = "mx0vglQjQRXyc807zR"
SECRET_KEY = "3446f56c2e914e8ea8aa411d1ae14b3e"
SPOT_BASE_URL = "https://api.mexc.com"  # MEXC Spot API base URL
FUTURES_BASE_URL = "https://contract.mexc.com"  # MEXC Futures API base URL

# Trading Type Configuration
TRADING_TYPE = "spot"  # Options: "spot" or "futures"

# Trading Configuration
SYMBOL = "AVAXUSDT"  # Trading pair

# Get the actual minimum requirements from MEXC
MIN_TRADE_USDT = 1.0  # MEXC typically requires at least $1 for most pairs
MIN_TRADE_BASE = 0.3  # Minimum amount of base currency to trade

# Trade amount in USDT (will be converted to appropriate base currency amount)
TRADE_AMOUNT_USDT = 1.01  # Set to at least the minimum required by exchange

# Fallback trade amount in base currency (used if price data is unavailable)
TRADE_AMOUNT_BASE = 1.01  # Set to at least the minimum required by exchange USDT

TIMEFRAME = "1m"    # Timeframe for analysis: 1m, 5m, 15m, 30m, 60m, 4h, 1d

# AI Strategy Configuration
RSI_PERIOD = 14       # RSI period
CONFIDENCE_THRESHOLD = 0.001  # Minimum confidence level for AI signals (0.0-1.0) 0-100%
PREDICTION_LENGTH = 10  # Number of future periods to predict

# Risk Management
STOP_LOSS_PCT = 0.02   # 2% stop loss
TAKE_PROFIT_PCT = 0.05 # 5% take profit
MAX_TRADES_PER_DAY = 10 # Maximum number of trades per day
MAX_POSITION_SIZE = 0.1 # Maximum position size as percentage of balance

# Historical Data
MAX_HISTORICAL_DATA = 200  # Number of historical data points to keep

# Trading Mode
REAL_TRADING = False  # Set to True for real trading (use with caution!)

# API Rate Limiting
REQUEST_DELAY = 0.1   # Delay between API requests in seconds
MAX_REQUESTS_PER_MINUTE = 60

# Notification Settings (optional)
TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None
SEND_NOTIFICATIONS = False

# Performance Monitoring
TRACK_PERFORMANCE = True
PERFORMANCE_REPORT_INTERVAL = 3600  # seconds (1 hour)

# Emergency Settings
AUTO_STOP_LOSS = True
MAX_DRAWDOWN_PCT = 0.1  # 10% maximum drawdown before stopping

# Futures Trading Settings (if applicable)
LEVERAGE = 10  # Leverage for futures trading
MARGIN_MODE = "ISOLATED"  # Margin mode: ISOLATED or CROSSED