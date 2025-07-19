# 📝 Logging System

MEXC⚡ implements a comprehensive logging system using the `loguru` library with the following features:

- **Multi-destination logging**: Console output and file logging simultaneously
- **Daily rotating log files**: `logs/MEXCL_YYYY-MM-DD.log`
- **30-day log retention**: Automatic cleanup of old logs
- **Configurable log level**: Set via `LOG_LEVEL` environment variable

## 📊 Log Levels
- `DEBUG`: Detailed debugging information (API requests, rate limiting details, order calculations)
- `INFO`: General operational information (order placement, trading status, symbol validation)
- `WARNING`: Warning messages for potential issues (outside trading hours, API fallbacks, dry run notices)
- `ERROR`: Error messages for failed operations (API errors, order failures, connectivity issues)
- `CRITICAL`: Critical errors requiring immediate attention (emergency stop-loss protocols)

## 📝 Log Format

#### File Output
```
YYYY-MM-DD HH:mm:ss | {level: <8} | {name}:{function}:{line} - {message}
```

## 🛠️ Configuration
1. Set log level in `.env`:
   ```
   LOG_LEVEL=INFO  # Options: DEBUG, INFO, WARNING, ERROR, CRITICAL
   ```

2. Log directory is automatically created at startup:
   ```python
   # From main.py
   os.makedirs("logs", exist_ok=True)
   ```

3. Logger initialization in TradingBot.initialize():
   ```python
   # Setup logging
   logger.remove()
   logger.add(
       sys.stderr,
       level=self.config.log_level,
       format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
   )
   logger.add(
       "logs/MEXCL_{time:YYYY-MM-DD}.log",
       rotation="1 day",
       retention="30 days",
       level=self.config.log_level,
       format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
   )
   ```

## 📖 Log Categories
MEXC⚡ logs various types of information across its components:

#### API Client (`mexc_client.py`)
- API requests and responses
- Rate limiting events and throttling
- API errors and connectivity issues
- Symbol validation results
- Server time synchronization

#### Trading Engine (`trading_engine.py`)
- Order placement and execution
- Position monitoring updates
- Stop-loss and take-profit triggers
- Trading window status checks
- Emergency protocols activation
- Daily counter resets

#### Main Bot (`main.py`)
- Bot initialization and shutdown
- Command-line argument processing
- Configuration validation
- Trading schedule management
- Error handling and recovery

## 🔄 Common Log Patterns

### Startup Sequence
```
INFO     | main:initialize:123 - Trading bot initialized successfully
WARNING  | main:initialize:126 - Bot is running in DRY RUN mode - no real trades will be executed
```

### API Interactions
```
INFO     | mexc_client:test_connectivity:224 - API connectivity test successful
INFO     | mexc_client:validate_symbol:201 - Symbol BTCUSDT validation: status=TRADING, spotTradingAllowed=True
```

### Trading Operations
```
INFO     | trading_engine:place_limit_buy_order:162 - Buy order placed: {'orderId': '12345', 'status': 'NEW'}
WARNING  | trading_engine:place_limit_buy_order:93 - Outside trading hours, skipping buy order
```

### Error Handling
```
ERROR    | mexc_client:_make_request:105 - API Error 400: {"code":-1121,"msg":"Invalid symbol."}
ERROR    | trading_engine:_execute_software_stop_loss:455 - Failed to execute stop loss: Connection timeout
```
