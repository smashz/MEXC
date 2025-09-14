import os
from pydantic import BaseModel, Field
from typing import Optional, List, Tuple
from dotenv import load_dotenv

load_dotenv()

class MexcCredentials(BaseModel):
    api_key: str = Field(..., description="MEXC API Key")
    secret_key: str = Field(..., description="MEXC Secret Key")
    passphrase: Optional[str] = Field(None, description="MEXC Passphrase if required")

class TradingParams(BaseModel):
    symbol: str = Field(..., description="Trading pair symbol (e.g., BTC_USDT)")
    timeframe: str = Field(default="5m", description="Trading timeframe")
    leverage: int = Field(default=1, ge=1, le=20, description="Trading leverage")
    dry_run: bool = Field(default=True, description="Enable dry run mode")
    trade_amount: float = Field(default=10.0, gt=0, description="Amount to trade in USDT")
    max_orders_per_day: int = Field(default=10, ge=1, le=100, description="Maximum orders per day")
    stop_loss_pct: float = Field(default=2.0, ge=0.1, le=50.0, description="Stop loss percentage")
    take_profit_pct: float = Field(default=3.0, ge=0.1, le=100.0, description="Take profit percentage")

class TimeWindow(BaseModel):
    start: str = Field(..., description="Trading window start time (HH:MM)")
    end: str = Field(..., description="Trading window end time (HH:MM)")
    enabled: bool = Field(default=True, description="Whether the time window is active")
    timezone: str = Field(default="UTC", description="Timezone for this trading window")
    
    @property
    def start_time(self) -> str:
        return self.start.strip('#') if self.start else "00:00"
        
    @property
    def end_time(self) -> str:
        return self.end.strip('#') if self.end else "23:59"

class AIConfig(BaseModel):
    model_path: str = Field(default="amazon/chronos-t5-small", description="Path to the AI model")
    prediction_length: int = Field(default=12, description="Number of periods to predict")
    lookback_periods: int = Field(default=24, description="Number of periods to look back")
    max_historical_data: int = Field(default=1000, description="Maximum historical data points to store")
    confidence_threshold: float = Field(default=0.65, ge=0.1, le=1.0, description="Minimum confidence threshold for AI predictions")
    min_trend_strength: float = Field(default=0.4, ge=0.1, le=1.0, description="Minimum trend strength threshold")
    update_interval: int = Field(default=300, description="Model update interval in seconds")
    feature_columns: List[str] = Field(default=["close", "volume", "high", "low"], description="Features to use for prediction")
    target_column: str = Field(default="close", description="Target column for prediction")
    timeframe: str = Field(default="5m", description="Trading timeframe")

class RiskConfig(BaseModel):
    max_drawdown_pct: float = Field(default=15.0, ge=1.0, le=50.0, description="Maximum drawdown percentage allowed")
    risk_per_trade_pct: float = Field(default=1.0, ge=0.1, le=5.0, description="Risk percentage per trade")
    position_sizing: str = Field(default="dynamic", description="Position sizing strategy")

class BotConfig(BaseModel):
    credentials: MexcCredentials
    trading_params: TradingParams
    trading_windows: List[TimeWindow] = Field(default=[], description="Active trading time windows")
    ai_config: AIConfig = Field(default_factory=AIConfig, description="AI trading configuration")
    risk_config: RiskConfig = Field(default_factory=RiskConfig, description="Risk management configuration")
    log_level: str = Field(default="INFO", description="Logging level")
    headless: bool = Field(default=False, description="Run in headless mode without curses UI")
    rate_limit_requests_per_second: float = Field(default=10.0, description="Rate limiting")
    track_metrics: bool = Field(default=True, description="Enable performance tracking")
    save_predictions: bool = Field(default=True, description="Save model predictions")
    
    class Config:
        env_file = ".env"

def load_config() -> BotConfig:
    """Load bot configuration from environment variables"""
    load_dotenv()
    
    # Load credentials
    credentials = MexcCredentials(
        api_key=os.getenv("API_KEY", ""),
        secret_key=os.getenv("API_SECRET", "")
    )
    
    # Load trading params
    trading_params = TradingParams(
        symbol=os.getenv("SYMBOL", "BTC_USDT"),
        timeframe=os.getenv("TIMEFRAME", "5m"),
        leverage=int(os.getenv("LEVERAGE", "1")),
        dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
        trade_amount=float(os.getenv("TRADE_AMOUNT", "10.0")),
        max_orders_per_day=int(os.getenv("MAX_ORDERS_PER_DAY", "10")),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "2.0")),
        take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
    )
    
    # Load AI config
    ai_config = AIConfig(
        model_path=os.getenv("MODEL_PATH", "amazon/chronos-t5-small"),
        prediction_length=int(os.getenv("PREDICTION_LENGTH", "12")),
        lookback_periods=int(os.getenv("LOOKBACK_PERIODS", "24")),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.65")),
        min_trend_strength=float(os.getenv("MIN_TREND_STRENGTH", "0.4")),
        update_interval=int(os.getenv("UPDATE_INTERVAL", "300")),
        feature_columns=os.getenv("FEATURE_COLUMNS", "close,volume,high,low").split(","),
        target_column=os.getenv("TARGET_COLUMN", "close"),
        timeframe=os.getenv("TIMEFRAME", "5m")
    )
    
    # Load risk config
    risk_config = RiskConfig(
        max_drawdown_pct=float(os.getenv("MAX_DRAWDOWN_PCT", "15.0")),
        risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "1.0")),
        position_sizing=os.getenv("POSITION_SIZING", "dynamic")
    )
    
    # Load trading windows
    trading_windows = []
    window_str = os.getenv("TRADING_WINDOWS", "")
    if window_str:
        for window in window_str.split(","):
            if "/" in window:
                start, end = window.split("/")
                trading_windows.append(TimeWindow(
                    start=start.strip(),
                    end=end.strip(),
                    enabled=True
                ))
                
    return BotConfig(
        credentials=credentials,
        trading_params=trading_params,
        trading_windows=trading_windows,
        ai_config=ai_config,
        dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
        headless=os.getenv("HEADLESS", "false").lower() == "true"
    )