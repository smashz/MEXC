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
    symbol: str = Field(..., description="Trading pair symbol (e.g., XRPUSDT)")
    quantity: float = Field(..., gt=0, description="Order quantity (in USDT if quantity_is_usdt=True, otherwise base currency)")
    quantity_is_usdt: bool = Field(default=True, description="Whether quantity is specified in USDT value")
    stop_loss_percentage: float = Field(default=2.0, ge=0.1, le=50.0, description="Stop loss percentage")
    take_profit_percentage: Optional[float] = Field(None, ge=0.1, le=100.0, description="Take profit percentage")
    max_orders_per_day: int = Field(default=10, ge=1, le=100, description="Maximum orders per day")

class TimeWindow(BaseModel):
    start_time: str = Field(..., description="Start time in HH:MM format")
    end_time: str = Field(..., description="End time in HH:MM format")
    timezone: str = Field(default="UTC", description="Timezone for trading window")

class BotConfig(BaseModel):
    credentials: MexcCredentials
    trading_params: TradingParams
    trading_windows: List[TimeWindow] = Field(default=[], description="Active trading time windows")
    dry_run: bool = Field(default=True, description="Enable dry run mode for testing")
    log_level: str = Field(default="INFO", description="Logging level")
    rate_limit_requests_per_second: float = Field(default=10.0, description="Rate limiting")
    
    class Config:
        env_file = ".env"

def load_config() -> BotConfig:
    """Load configuration from environment variables and defaults"""
    credentials = MexcCredentials(
        api_key=os.getenv("MEXC_API_KEY", ""),
        secret_key=os.getenv("MEXC_SECRET_KEY", ""),
        passphrase=os.getenv("MEXC_PASSPHRASE", "")
    )
    
    trading_params = TradingParams(
        symbol=os.getenv("TRADING_SYMBOL", "XRPUSDT"),
        quantity=float(os.getenv("TRADING_QUANTITY", "2.0")),
        quantity_is_usdt=os.getenv("QUANTITY_IS_USDT", "true").lower() == "true",
        stop_loss_percentage=float(os.getenv("STOP_LOSS_PERCENTAGE", "2.0")),
        take_profit_percentage=float(os.getenv("TAKE_PROFIT_PERCENTAGE", "5.0")) if os.getenv("TAKE_PROFIT_PERCENTAGE") else None,
    )
    
    trading_windows = []
    if os.getenv("TRADING_START_TIME") and os.getenv("TRADING_END_TIME"):
        trading_windows.append(TimeWindow(
            start_time=os.getenv("TRADING_START_TIME"),
            end_time=os.getenv("TRADING_END_TIME"),
            timezone=os.getenv("TRADING_TIMEZONE", "UTC")
        ))
    
    return BotConfig(
        credentials=credentials,
        trading_params=trading_params,
        trading_windows=trading_windows,
        dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        rate_limit_requests_per_second=float(os.getenv("RATE_LIMIT_RPS", "10.0"))
    )
