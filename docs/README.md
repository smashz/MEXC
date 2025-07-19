

## 📈 Trading Strategies & Order Types

### 1. **🎯 Sequential Bracket Orders**

This strategy places entry, stop-loss, and take-profit orders in sequence with automatic monitoring.

#### Workflow:
1. **Entry**: Places limit buy order
2. **Monitor**: Waits for entry fill
3. **Protection**: Places stop-loss and take-profit orders after entry fills
4. **Management**: Monitors orders and handles cancellations

#### Usage Example:
```bash
python main.py --action sequential \
  --symbol BTCUSDT \
  --price 45000 \
  --stop-loss 44000 \
  --take-profit 47000 \
  --quantity 100
```

### 2. **🎯 Simple Bracket Orders**

Places all orders (entry, stop-loss, take-profit) simultaneously.

```bash
python main.py --action bracket \
  --symbol BTCUSDT \
  --price 45000 \
  --stop-loss 44000 \
  --take-profit 47000 \
  --quantity 100
```

### 3. **📋 Basic Limit Orders**

#### USDT-Based Quantities
```bash
# Buy $100 worth of crypto
python main.py --action buy \
  --symbol BTCUSDT \
  --price 45000 \
  --quantity 100
```

### 4. **⏰ Time-Scheduled Orders**

Schedule orders for specific execution times:

```bash
python main.py --action buy \
  --symbol BTCUSDT \
  --price 45000 \
  --quantity 100 \
  --time "14:30"
```

- Timezone support (configurable via `TRADING_TIMEZONE`)
- Countdown display for execution
- Automatic next-day scheduling if time has passed

### 5. **🔍 Market Analysis Tools**

```bash
# Search available trading pairs
python main.py --action symbols --search BTC

# Validate trading pair
python main.py --action validate --symbol BTCUSDT

# Test API permissions
python main.py --action test
```


