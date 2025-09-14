import pytz
import asyncio
import numpy as np
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from loguru import logger
from mexc_client import MexcClient
from config import BotConfig, TimeWindow, AIConfig
from chronos_strategy import ChronosTradingStrategy

class TradingEngine:
    """High-performance trading engine with stop-loss, time windows, and order management"""
    
    def __init__(self, config: BotConfig, client: MexcClient):
        self.config = config
        self.client = client
        self.chronos = ChronosTradingStrategy(config)  # Initialize with config
        self.last_signal = None
        self.last_price = None
        self.historical_prices = []
        self._running = False
        self._stop_event = asyncio.Event()
        self.ui_update_callback = None
        # Trading state
        self.positions = {}  # Dictionary to store positions by order ID
        self.orders = []
        self.signals = []
        self.daily_trades = 0
        self.daily_order_count = 0  # Track daily order count
        
        # Rate limiting and updates
        self._last_price_update = 0
        self._last_klines = []
        self._last_signal_time = 0
        self._min_signal_interval = 60  # Minimum seconds between signals
        
        # Performance metrics
        self.total_profit = 0.0
        self.win_count = 0
        self.loss_count = 0
        
    async def reload_config(self):
        """Reload configuration from .env and reinitialize components"""
        from config import load_config
        try:
            # Load fresh config from .env
            new_config = load_config()
            old_symbol = self.config.trading_params.symbol
            
            # Standardize both old and new symbols
            old_symbol_std = self._standardize_symbol(old_symbol)
            new_symbol_std = self._standardize_symbol(new_config.trading_params.symbol)
            new_config.trading_params.symbol = new_symbol_std
            
            # Update config
            self.config = new_config
            
            if old_symbol_std != new_symbol_std:
                logger.info(f"Trading symbol changed from {old_symbol_std} to {new_symbol_std}")
                # Reinitialize strategy with new config
                self.chronos = ChronosTradingStrategy(new_config)
                
                # Clear all historical data and state
                self.historical_prices = []
                self._last_klines = []
                self.last_price = None
                self.last_signal = None
                self.positions = {}  # Clear any tracked positions
                self.signals = []    # Clear signal history
                
                # Reset trading metrics for new symbol
                self.daily_trades = 0
                self.total_profit = 0.0
                self.win_count = 0
                self.loss_count = 0
                
                # Reinitialize with new symbol
                await self.initialize()
                logger.info(f"Trading engine reinitialized with new symbol: {new_symbol_std}")
            else:
                # Update the strategy config even if symbol hasn't changed
                self.chronos.config = new_config
                logger.info(f"Configuration updated, symbol unchanged: {new_symbol_std}")
            
            return True
        except Exception as e:
            logger.error(f"Error reloading configuration: {str(e)}")
            logger.debug(f"Stack trace:", exc_info=True)
            return False
        
    def _standardize_symbol(self, symbol: str) -> str:
        """Standardize symbol format to MEXC's requirements"""
        # Remove any existing underscore
        symbol = symbol.replace("_", "")
        # If the symbol doesn't contain USDT, we assume it's a USDT pair
        if "USDT" not in symbol.upper():
            symbol = f"{symbol}USDT"
        return symbol.upper()
    
    async def initialize(self):
        """Initialize the trading engine and load historical data"""
        logger.info("Initializing trading engine...")
        
        try:
            # Standardize symbol format
            raw_symbol = self.config.trading_params.symbol
            self.config.trading_params.symbol = self._standardize_symbol(raw_symbol)
            logger.debug(f"Standardized symbol: {raw_symbol} -> {self.config.trading_params.symbol}")
            
            # Ensure client is initialized
            if not self.client or not self.client.session:
                logger.error("API client not properly initialized")
                return False
                
            logger.debug("Client and session are properly initialized")
                
            # Test API connection
            logger.info("Testing API connection...")
            try:
                await self.client.get_klines(
                    symbol=self.config.trading_params.symbol,
                    interval=self.config.ai_config.timeframe,
                    limit=1
                )
            except Exception as e:
                logger.error(f"API connection test failed: {e}")
                return False
                
            # Fetch historical data with timeout
            async with asyncio.timeout(30):  # 30 second timeout
                if not await self._fetch_historical_data():
                    logger.error("Failed to initialize historical data")
                    return False
            
            logger.info("Trading engine initialized successfully")
            self._running = True
            return True
            
        except asyncio.TimeoutError:
            logger.error("Initialization timed out while waiting for data")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize trading engine: {e}")
            return False
            
    async def run_strategy(self):
        """Main strategy loop that updates AI model and generates trading signals"""
        while self._running and not self._stop_event.is_set():
            try:
                # Check trading time window
                if not await self.is_trading_time():
                    logger.info("Outside trading hours, waiting...")
                    await asyncio.sleep(60)
                    continue
                
                # Rate limiting
                current_time = time.time()
                if current_time - self._last_signal_time < self._min_signal_interval:
                    await asyncio.sleep(1)
                    continue
                
                # Update price and model with timeout
                try:
                    async with asyncio.timeout(10):  # 10 second timeout for price updates
                        current_price = await self.get_current_price(self.config.trading_params.symbol)
                        if not current_price:
                            logger.warning("No current price available")
                            await asyncio.sleep(5)
                            continue
                        
                        logger.debug(f"Got current price: {current_price}")
                        self.last_price = current_price
                        self.chronos.update_data(current_price)
                        
                        # Generate AI prediction and trading signal
                        prediction_info = self.chronos.get_last_prediction()
                        signal = prediction_info['direction']  # The direction is our signal
                        
                        # Process signal if confidence threshold is met
                        if prediction_info.get('confidence', 0) >= self.config.ai_config.confidence_threshold:
                            logger.info(f"Signal generated: {signal} (confidence: {prediction_info.get('confidence', 0):.2f})")
                            
                            # Record signal
                            signal_record = {
                                'type': signal,
                                'price': current_price,
                                'confidence': prediction_info['confidence'],
                                'direction': prediction_info.get('direction'),
                                'timestamp': datetime.now().isoformat()
                            }
                            self.signals.append(signal_record)
                            
                            # Execute trade
                            if not self.config.dry_run:
                                await self._execute_signal_trade(signal, current_price)
                            else:
                                logger.info(f"[DRY RUN] Would execute {signal} trade at {current_price}")
                            
                            # Update UI
                            if self.ui_update_callback:
                                await self.ui_update_callback(current_price, prediction_info)
                            
                            self.last_signal = signal
                            self._last_signal_time = current_time
                        
                except asyncio.TimeoutError:
                    logger.error("Timeout while fetching current price")
                    await asyncio.sleep(5)
                    continue
                except Exception as e:
                    logger.error(f"Error processing signal: {str(e)}")
                    await asyncio.sleep(5)
                    continue
                
                # Sleep before next update
                await asyncio.sleep(self.config.ai_config.update_interval)
                
            except Exception as e:
                logger.error(f"Error in trading loop: {str(e)}")
                await asyncio.sleep(5)
                
    async def _fetch_historical_data(self):
        """Fetch historical price data for initialization"""
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries:
            try:
                logger.info(f"Fetching historical data (attempt {retry_count + 1}/{max_retries})...")
                klines = await self.client.get_klines(
                    symbol=self.config.trading_params.symbol,
                    interval=self.config.ai_config.timeframe,
                    limit=self.config.ai_config.max_historical_data
                )
                
                if not klines:
                    logger.warning("No historical data received")
                    retry_count += 1
                    await asyncio.sleep(2)
                    continue
                
                logger.info(f"Received {len(klines)} historical price points")
                self._last_klines = klines
                self.historical_prices = []
                
                for kline in klines:
                    try:
                        close_price = float(kline[4])  # Close price
                        self.historical_prices.append(close_price)
                        self.chronos.update_data(close_price)
                    except (IndexError, ValueError) as e:
                        logger.error(f"Invalid kline data format: {e}")
                        continue
                
                if self.historical_prices:
                    return True
                logger.error("No valid prices in historical data")
                return False
                
            except Exception as e:
                logger.error(f"Error fetching historical data: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(2)
                    
        logger.error("Failed to fetch historical data after max retries")
        return False
        
        logger.info("Trading engine initialized successfully")
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.daily_order_count = 0
        self.last_reset_date = datetime.now().date()
        self.stop_loss_orders: Dict[str, int] = {}  # position_id -> stop_loss_order_id
        
    async def is_trading_time(self) -> bool:
        """Check if current time is within any trading window"""
        if not self.config.trading_windows:
            return True  # Trade 24/7 if no windows specified
        
        now = datetime.now()
        
        for window in self.config.trading_windows:
            # Convert window timezone
            tz = pytz.timezone(window.timezone)
            local_now = now.astimezone(tz)
            
            # Parse time strings
            start_time = datetime.strptime(window.start_time, "%H:%M").time()
            end_time = datetime.strptime(window.end_time, "%H:%M").time()
            current_time = local_now.time()
            
            # Handle overnight windows (e.g., 22:00 to 06:00)
            if start_time <= end_time:
                if start_time <= current_time <= end_time:
                    return True
            else:
                if current_time >= start_time or current_time <= end_time:
                    return True
        
        return False
    
    def _reset_daily_counters(self):
        """Reset daily counters if new day"""
        today = datetime.now().date()
        if today != self.last_reset_date:
            self.daily_order_count = 0
            self.last_reset_date = today
            logger.info("Daily counters reset")
    
    def _can_place_order(self) -> bool:
        """Check if we can place more orders today"""
        self._reset_daily_counters()
        return self.daily_order_count < self.config.trading_params.max_orders_per_day
    
    def _calculate_order_quantity(self, price: float, usdt_amount: Optional[float] = None) -> float:
        """Calculate order quantity based on USDT amount or configured quantity"""
        if self.config.trading_params.quantity_is_usdt:
            # Calculate quantity from USDT amount
            usdt_value = usdt_amount or self.config.trading_params.quantity
            return usdt_value / price
        else:
            # Use direct quantity (base currency amount)
            return usdt_amount or self.config.trading_params.quantity
    
    def _get_available_balance(self, account_info: Dict[str, Any], asset: str) -> float:
        """Get available balance for a specific asset from account info"""
        try:
            balances = account_info.get('balances', [])
            for balance in balances:
                if balance.get('asset') == asset:
                    return float(balance.get('free', 0))
            return 0.0
        except Exception as e:
            logger.error(f"Error getting available balance for {asset}: {str(e)}")
            return 0.0
    
    def _calculate_stop_loss_price(self, entry_price: float, side: str) -> float:
        """Calculate stop loss price based on entry price and percentage"""
        stop_loss_percentage = self.config.trading_params.stop_loss_percentage / 100
        
        if side == 'BUY':
            # For long positions, stop loss is below entry price
            return entry_price * (1 - stop_loss_percentage)
        else:
            # For short positions, stop loss is above entry price
            return entry_price * (1 + stop_loss_percentage)
    
    def _calculate_take_profit_price(self, entry_price: float, side: str) -> Optional[float]:
        """Calculate take profit price if configured"""
        if not self.config.trading_params.take_profit_percentage:
            return None
            
        take_profit_percentage = self.config.trading_params.take_profit_percentage / 100
        
        if side == 'BUY':
            # For long positions, take profit is above entry price
            return entry_price * (1 + take_profit_percentage)
        else:
            # For short positions, take profit is below entry price
            return entry_price * (1 - take_profit_percentage)
    
    async def get_current_price(self, symbol: str) -> float:
        """Get current market price for symbol"""
        try:
            ticker = await self.client.get_ticker_price(symbol)
            return float(ticker['price'])
        except Exception as e:
            logger.error(f"Failed to get current price for {symbol}: {str(e)}")
            raise
    
    async def update_strategy(self):
        """Update the Chronos trading strategy with latest market data"""
        try:
            # Get current market data
            symbol = self.config.trading_params.symbol
            logger.debug(f"Fetching current price for {symbol}")
            
            ticker = await self.client.get_ticker_price(symbol)
            logger.debug(f"Received ticker response: {ticker}")
            
            current_price = float(ticker.get('price', 0))
            logger.debug(f"Parsed current price: {current_price}")
            
            if not current_price:
                logger.warning("Got zero or invalid price from ticker")
                return None
                
            self.last_price = current_price
            self.chronos.update_data(current_price)
            
            # Generate predictions and signals
            prediction = self.chronos.get_last_prediction()
            signal = prediction.get('direction', 'NEUTRAL')
            
            # Update signal history
            self.signals.append({
                "type": signal,
                "price": current_price,
                "confidence": prediction.get("confidence", 0),
                "reason": f"Signal:{prediction.get('direction')} Conf:{prediction.get('confidence', 0):.1%}",
                "timestamp": datetime.now()
            })
            
            # Keep signal list manageable
            if len(self.signals) > 100:
                self.signals = self.signals[-100:]
            
            # Execute trades if conditions are met
            if not self.config.dry_run and signal != self.last_signal:
                await self._execute_signal_trade(signal, current_price)
            
            self.last_signal = signal
            return prediction
            
        except Exception as e:
            logger.error(f"Error updating strategy: {e}")
            return None
            
    async def _execute_signal_trade(self, signal: str, price: float) -> None:
        """Execute trades based on strategy signals"""
        try:
            if signal == "BUY" and self.daily_trades < self.config.trading_params.max_trades_per_day:
                # Calculate position size
                quantity = self.config.trading_params.quantity
                
                # Place buy order
                order = await self.place_limit_buy_order(price, quantity)
                if order:
                    self.orders.append(order)
                    self.daily_trades += 1
                    
            elif signal == "SELL" and self.positions:
                # Close any open positions
                for position in self.positions:
                    await self.place_limit_sell_order(
                        price,
                        position.get("quantity"),
                        position.get("order_id")
                    )
                    
        except Exception as e:
            logger.error(f"Error executing trade for signal {signal}: {e}")
            
    async def _check_daily_reset(self):
        """Reset daily trade counter at UTC midnight"""
        now = datetime.now()
        if now.hour == 0 and now.minute == 0:
            self.daily_trades = 0
    
    async def start(self):
        """Start the trading engine"""
        self._running = True
        while self._running:
            try:
                if await self.is_trading_time():
                    await self.update_strategy()
                await asyncio.sleep(self.config.ai_config.update_interval)
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                await asyncio.sleep(5)  # Back off on error
                
    async def stop(self):
        """Stop the trading engine"""
        self._running = False
        self._stop_event.set()
    
    async def place_limit_buy_order(self, price: float, quantity: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Place a limit buy order with automatic stop loss using MEXC's integrated approach"""
        if not await self.is_trading_time():
            logger.warning("Outside trading hours, skipping buy order")
            return None
        
        if not self._can_place_order():
            logger.warning("Daily order limit reached, skipping buy order")
            return None
        
        symbol = self.config.trading_params.symbol
        
        # Calculate order quantity (handles both USDT and base currency modes)
        order_quantity = self._calculate_order_quantity(price, quantity)
        
        # Round to appropriate precision (make this configurable?)
        order_quantity = round(order_quantity, 6)
        
        try:
            if self.config.dry_run:
                usdt_value = order_quantity * price
                logger.info(f"DRY RUN: Would place BUY order for {order_quantity} {symbol} at {price} (${usdt_value:.2f} USDT)")
                return {"orderId": f"dry_run_{int(time.time())}", "side": "BUY", "price": price, "quantity": order_quantity}
            
            # Calculate stop-loss price
            stop_loss_price = self._calculate_stop_loss_price(price, 'BUY')
            
            # Try to place limit order with integrated stop-loss first
            try:
                order_result = await self.client.place_limit_order_with_stop_loss(
                    symbol=symbol,
                    side='BUY',
                    quantity=order_quantity,
                    price=price,
                    stop_price=stop_loss_price
                )
                
                logger.info(f"Buy order with integrated stop-loss placed: {order_result}")
                self.daily_order_count += 1
                
                # Store position with integrated stop-loss info
                self.positions[order_result['orderId']] = {
                    'entry_price': price,
                    'quantity': order_quantity,
                    'stop_loss_price': stop_loss_price,
                    'side': 'BUY',
                    'timestamp': time.time(),
                    'integrated_stop_loss': True  # Flag indicating stop-loss is built into the order
                }
                
                return order_result
                
            except Exception as e:
                logger.warning(f"Integrated stop-loss order failed: {str(e)}")
                logger.info("Falling back to regular limit order with software stop-loss...")
                
                # Fallback to regular limit order
                order_result = await self.client.place_limit_order(
                    symbol=symbol,
                    side='BUY',
                    quantity=order_quantity,
                    price=price
                )
                
                logger.info(f"Buy order placed: {order_result}")
                self.daily_order_count += 1
                
                # Setup software-based stop-loss monitoring
                await self._setup_stop_loss(order_result['orderId'], price, order_quantity, stop_loss_price)
                
                return order_result
            
        except Exception as e:
            logger.error(f"Failed to place buy order: {str(e)}")
            raise
    
    async def place_limit_sell_order(self, price: float, quantity: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Place a limit sell order with automatic stop loss using MEXC's integrated approach"""
        if not await self.is_trading_time():
            logger.warning("Outside trading hours, skipping sell order")
            return None
        
        if not self._can_place_order():
            logger.warning("Daily order limit reached, skipping sell order")
            return None
            
        if len(self.positions) >= self.config.trading_params.max_orders_per_day:
            logger.warning("Maximum number of concurrent positions reached")
            return None
        
        symbol = self.config.trading_params.symbol
        
        # Calculate order quantity (handles both USDT and base currency modes)
        order_quantity = self._calculate_order_quantity(price, quantity)
        
        # Round to appropriate precision (make this configurable?)
        order_quantity = round(order_quantity, 6)
        
        try:
            if self.config.dry_run:
                usdt_value = order_quantity * price
                logger.info(f"DRY RUN: Would place SELL order for {order_quantity} {symbol} at {price} (${usdt_value:.2f} USDT)")
                return {"orderId": f"dry_run_{int(time.time())}", "side": "SELL", "price": price, "quantity": order_quantity}
            
            # Calculate stop-loss price
            stop_loss_price = self._calculate_stop_loss_price(price, 'SELL')
            
            # Try to place limit order with integrated stop-loss first
            try:
                order_result = await self.client.place_limit_order_with_stop_loss(
                    symbol=symbol,
                    side='SELL',
                    quantity=order_quantity,
                    price=price,
                    stop_price=stop_loss_price
                )
                
                logger.info(f"Sell order with integrated stop-loss placed: {order_result}")
                self.daily_order_count += 1
                
                # Store position with integrated stop-loss info
                self.positions[order_result['orderId']] = {
                    'entry_price': price,
                    'quantity': order_quantity,
                    'stop_loss_price': stop_loss_price,
                    'side': 'SELL',
                    'timestamp': time.time(),
                    'integrated_stop_loss': True  # Flag indicating stop-loss is built into the order
                }
                
                return order_result
                
            except Exception as e:
                logger.warning(f"Integrated stop-loss order failed: {str(e)}")
                logger.info("Falling back to regular limit order with software stop-loss...")
                
                # Fallback to regular limit order
                order_result = await self.client.place_limit_order(
                    symbol=symbol,
                    side='SELL',
                    quantity=order_quantity,
                    price=price
                )
                
                logger.info(f"Sell order placed: {order_result}")
                self.daily_order_count += 1
                
                # Setup software-based stop-loss monitoring
                await self._setup_stop_loss(order_result['orderId'], price, order_quantity, stop_loss_price)
                
                return order_result
            
        except Exception as e:
            logger.error(f"Failed to place sell order: {str(e)}")
            raise
    
    async def _setup_stop_loss(self, position_id: str, entry_price: float, quantity: float, stop_loss_price: float):
        """Set up stop loss order on MEXC exchange"""
        try:
            symbol = self.config.trading_params.symbol
            
            if self.config.dry_run:
                logger.info(f"DRY RUN: Would place stop loss order at {stop_loss_price} for position {position_id}")
                return
            
            # Determine opposite side for stop-loss order (sell to close long position)
            stop_loss_side = 'SELL'  # Assuming we're protecting a long position (BUY order)
            
            # Place actual stop-loss order on MEXC exchange
            try:
                stop_loss_result = await self.client.place_stop_loss_order(
                    symbol=symbol,
                    side=stop_loss_side,
                    quantity=quantity,
                    stop_price=stop_loss_price
                )
                
                # Store the stop-loss order ID for later management
                self.stop_loss_orders[position_id] = stop_loss_result['orderId']
                
                logger.info(f"Stop loss order placed on exchange: {stop_loss_result['orderId']} at price {stop_loss_price}")
                
                # Also store position for monitoring (as backup)
                self.positions[position_id] = {
                    'entry_price': entry_price,
                    'quantity': quantity,
                    'stop_loss_price': stop_loss_price,
                    'stop_loss_order_id': stop_loss_result['orderId'],
                    'side': 'BUY',
                    'timestamp': time.time()
                }
                
            except Exception as e:
                logger.error(f"Failed to place stop-loss order on exchange: {str(e)}")
                logger.info("Falling back to software-based stop-loss monitoring...")
                
                # Fallback to software monitoring if exchange order fails
                self.positions[position_id] = {
                    'entry_price': entry_price,
                    'quantity': quantity,
                    'stop_loss_price': stop_loss_price,
                    'side': 'BUY',
                    'timestamp': time.time(),
                    'fallback_monitoring': True  # Flag to indicate software monitoring
                }
                
                logger.info(f"Software-based stop loss monitoring activated at {stop_loss_price} for position {position_id}")
            
        except Exception as e:
            logger.error(f"Failed to setup stop loss: {str(e)}")
            # Don't let stop-loss errors prevent the main order from succeeding
    
    async def monitor_positions(self):
        """Monitor all active positions for stop-loss triggers and bracket completion"""
        logger.info("Starting position monitoring...")
        
        # Ensure positions attribute exists
        if not hasattr(self, 'positions'):
            logger.warning("positions attribute not found, initializing empty dictionary")
            self.positions = {}
        
        while True:
            try:
                positions_to_check = list(self.positions.items())
                
                for position_id, position_data in positions_to_check:
                    try:
                        # Handle different types of position monitoring
                        bracket_type = position_data.get('bracket_type', 'standard')
                        
                        if bracket_type == 'sequential':
                            # Monitor sequential bracket orders (new functionality)
                            await self._monitor_sequential_bracket_position(position_id, position_data)
                        elif position_data.get('bracket_monitoring'):
                            # Monitor existing bracket orders
                            await self._monitor_bracket_position(position_id, position_data)
                        else:
                            # Monitor standard positions with software stop-loss
                            await self._check_position_status(position_id, position_data)
                            
                    except Exception as e:
                        logger.error(f"Error checking position {position_id}: {str(e)}")
                
                # Wait before next check (adjust?)
                await asyncio.sleep(0.1)  # Check every 0.1 seconds for faster stop loss execution
                
            except Exception as e:
                logger.error(f"Error in position monitoring loop: {str(e)}")
                await asyncio.sleep(1)  # Wait longer on error (?)
    
    async def _check_position_status(self, position_id: str, position_data: Dict[str, Any]):
        """Check status of a specific position and handle stop-loss triggers"""
        try:
            symbol = self.config.trading_params.symbol
            
            # Check if main order is filled
            order_status = await self.client.get_order_status(symbol, int(position_id))
            
            if order_status['status'] == 'FILLED':
                # Check if this position has integrated stop-loss
                if position_data.get('integrated_stop_loss', False):
                    # For integrated stop-loss orders, MEXC handles everything automatically
                    # We just need to monitor the position and log status
                    current_price = await self.get_current_price(symbol)
                    entry_price = position_data['entry_price']
                    stop_loss_price = position_data['stop_loss_price']
                    side = position_data['side']
                    
                    # Calculate PnL
                    pnl_percent = ((current_price - entry_price) / entry_price) * 100
                    if side == 'SELL':
                        pnl_percent = -pnl_percent
                    
                    # Check if we're close to stop-loss trigger
                    if side == 'BUY' and current_price <= stop_loss_price * 1.01:  # Within 1% of stop-loss
                        logger.warning(f"Position {position_id} approaching stop-loss: current={current_price}, stop-loss={stop_loss_price}")
                    elif side == 'SELL' and current_price >= stop_loss_price * 0.99:  # Within 1% of stop-loss
                        logger.warning(f"Position {position_id} approaching stop-loss: current={current_price}, stop-loss={stop_loss_price}")
                    else:
                        logger.info(f"Position {position_id} active (integrated SL): entry={entry_price}, current={current_price}, PnL={pnl_percent:.2f}%, SL={stop_loss_price}")
                
                # Check if this position has an exchange-based stop-loss order (fallback method)
                elif 'stop_loss_order_id' in position_data and not position_data.get('fallback_monitoring', False):
                    # Exchange-based stop-loss - just check if it's still active
                    stop_loss_order_id = position_data['stop_loss_order_id']
                    try:
                        sl_status = await self.client.get_order_status(symbol, int(stop_loss_order_id))
                        if sl_status['status'] == 'FILLED':
                            logger.info(f"Stop-loss order {stop_loss_order_id} executed for position {position_id}")
                            await self._cleanup_position(position_id)
                        elif sl_status['status'] == 'CANCELED':
                            logger.warning(f"Stop-loss order {stop_loss_order_id} was canceled for position {position_id}")
                            await self._cleanup_position(position_id)
                        else:
                            # Stop-loss order is still active, just log position status
                            current_price = await self.get_current_price(symbol)
                            entry_price = position_data['entry_price']
                            pnl_percent = ((current_price - entry_price) / entry_price) * 100
                            logger.info(f"Position {position_id} active: entry={entry_price}, current={current_price}, PnL={pnl_percent:.2f}%, SL Order={stop_loss_order_id}")
                    except Exception as e:
                        logger.error(f"Error checking stop-loss order {stop_loss_order_id}: {e}")
                        # If we can't check the stop-loss order, fall back to software monitoring
                        position_data['fallback_monitoring'] = True
                        logger.info(f"Switching to software monitoring for position {position_id}")
                
                # Software-based monitoring (fallback or primary if exchange orders failed)
                elif position_data.get('fallback_monitoring', False) or 'stop_loss_order_id' not in position_data:
                    current_price = await self.get_current_price(symbol)
                    stop_loss_price = position_data['stop_loss_price']
                    entry_price = position_data['entry_price']
                    side = position_data['side']
                    quantity = position_data['quantity']
                    
                    should_trigger_stop_loss = False
                    
                    if side == 'BUY':
                        # For long positions, trigger if price drops below stop-loss
                        if current_price <= stop_loss_price:
                            should_trigger_stop_loss = True
                            logger.warning(f"Software stop-loss triggered for BUY position {position_id}: current_price={current_price} <= stop_loss={stop_loss_price}")
                    else:
                        # For short positions, trigger if price rises above stop-loss
                        if current_price >= stop_loss_price:
                            should_trigger_stop_loss = True
                            logger.warning(f"Software stop-loss triggered for SELL position {position_id}: current_price={current_price} >= stop_loss={stop_loss_price}")
                    
                    if should_trigger_stop_loss:
                        await self._execute_software_stop_loss(position_id, position_data, current_price)
                    else:
                        # Log position status periodically
                        pnl_percent = ((current_price - entry_price) / entry_price) * 100
                        if side == 'SELL':
                            pnl_percent = -pnl_percent
                        logger.info(f"Position {position_id} active (software monitoring): entry={entry_price}, current={current_price}, PnL={pnl_percent:.2f}%")
                
            elif order_status['status'] == 'CANCELED':
                # Clean up if main order was canceled
                logger.info(f"Main order {position_id} was canceled, cleaning up position")
                await self._cleanup_position(position_id)
                
        except Exception as e:
            logger.error(f"Error checking position {position_id}: {str(e)}")
    
    async def _execute_software_stop_loss(self, position_id: str, position_data: Dict[str, Any], current_price: float):
        """Execute software-based stop-loss by placing a market order"""
        try:
            symbol = self.config.trading_params.symbol
            quantity = position_data['quantity']
            side = position_data['side']
            entry_price = position_data['entry_price']
            
            # Determine opposite side for stop-loss order
            stop_loss_side = 'SELL' if side == 'BUY' else 'BUY'
            
            if self.config.dry_run:
                loss_amount = (entry_price - current_price) * quantity
                logger.warning(f"DRY RUN: Would execute software stop-loss {stop_loss_side} order for {quantity} {symbol} at market price ~{current_price} (loss: ${loss_amount:.2f})")
                await self._cleanup_position(position_id)
                return
            
            # Place market order to close position immediately
            stop_loss_result = await self.client.place_market_order(
                symbol=symbol,
                side=stop_loss_side,
                quantity=quantity
            )
            
            loss_amount = abs((entry_price - current_price) * quantity)
            logger.warning(f"Software stop-loss executed: {stop_loss_result['orderId']} - Loss: ${loss_amount:.2f}")
            
            # Clean up position
            await self._cleanup_position(position_id)
            
        except Exception as e:
            logger.error(f"Failed to execute software stop-loss for position {position_id}: {str(e)}")
            # Try to clean up anyway
            await self._cleanup_position(position_id)
    
    async def _cleanup_position(self, position_id: str):
        """Clean up a position and its associated stop loss"""
        try:
            # Cancel stop loss order if exists
            if position_id in self.stop_loss_orders:
                stop_loss_id = self.stop_loss_orders[position_id]
                try:
                    await self.client.cancel_order(self.config.trading_params.symbol, stop_loss_id)
                    logger.info(f"Canceled stop loss order {stop_loss_id}")
                except:
                    pass  # Stop loss might already be executed
                
                del self.stop_loss_orders[position_id]
            
            # Remove from active positions
            if position_id in self.positions:
                del self.positions[position_id]
                
            logger.info(f"Cleaned up position {position_id}")
            
        except Exception as e:
            logger.error(f"Error cleaning up position {position_id}: {str(e)}")
    
    async def cancel_order(self, order_id: int) -> bool:
        """Cancel an order and clean up associated data"""
        try:
            symbol = self.config.trading_params.symbol
            result = await self.client.cancel_order(symbol, order_id)
            
            # Clean up position data
            order_id_str = str(order_id)
            await self._cleanup_position(order_id_str)
            
            logger.info(f"Order {order_id} canceled successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {str(e)}")
            return False
    
    async def get_account_summary(self) -> Dict[str, Any]:
        """Get trading account summary"""
        try:
            account_info = await self.client.get_account_info()
            open_orders = await self.client.get_open_orders()
            current_price = await self.get_current_price(self.config.trading_params.symbol)
            
            return {
                "account_balance": account_info.get('balances', []),
                "open_orders_count": len(open_orders),
                "active_positions_count": len(self.positions),
                "daily_orders_used": self.daily_order_count,
                "daily_orders_remaining": self.config.trading_params.max_orders_per_day - self.daily_order_count,
                "current_price": current_price,
                "trading_time_active": await self.is_trading_time(),
                "quantity_mode": "USDT-based" if self.config.trading_params.quantity_is_usdt else "Base currency",
                "configured_quantity": self.config.trading_params.quantity
            }
            
        except Exception as e:
            logger.error(f"Failed to get account summary: {str(e)}")
            return {"error": str(e)}
    
    async def place_bracket_buy_order(
        self, 
        price: float, 
        quantity: Optional[float] = None,
        stop_loss_percentage: float = 5.0,
        take_profit_percentage: float = 10.0
    ) -> Optional[Dict[str, Any]]:
        """Place a buy order with both stop-loss and take-profit (bracket order)"""
        
        symbol = self.config.trading_params.symbol
        
        # Calculate quantity if not provided
        if quantity is None:
            account_info = await self.client.get_account_info()
            available_balance = self._get_available_balance(account_info, 'USDT')
            
            # Use configured allocation percentage
            usable_balance = available_balance * (self.config.trading_params.allocation_percentage / 100)
            quantity = usable_balance / price
            
            logger.info(f"Auto-calculated quantity: {quantity} (from ${usable_balance:.2f} USDT)")
        
        if self.config.dry_run:
            logger.info("DRY RUN: Would place bracket buy order")
            logger.info(f"  Symbol: {symbol}")
            logger.info(f"  Entry Price: ${price}")
            logger.info(f"  Quantity: {quantity}")
            logger.info(f"  Stop Loss: {stop_loss_percentage}% (${price * (1 - stop_loss_percentage/100):.4f})")
            logger.info(f"  Take Profit: {take_profit_percentage}% (${price * (1 + take_profit_percentage/100):.4f})")
            return {
                'dry_run': True,
                'symbol': symbol,
                'side': 'BUY',
                'quantity': quantity,
                'price': price,
                'stop_loss_percentage': stop_loss_percentage,
                'take_profit_percentage': take_profit_percentage
            }
        
        try:
            # Place the bracket order
            result = await self.client.place_bracket_order(
                symbol=symbol,
                side='BUY',
                quantity=quantity,
                price=price,
                stop_loss_percentage=stop_loss_percentage,
                take_profit_percentage=take_profit_percentage
            )
            
            logger.info(f"Bracket buy order placed successfully:")
            logger.info(f"  Order ID: {result['main_order'].get('orderId', 'Unknown')}")
            logger.info(f"  Bracket Type: {result['bracket_type']}")
            logger.info(f"  Stop Loss: ${result['stop_loss_price']:.4f}")
            logger.info(f"  Take Profit: ${result['take_profit_price']:.4f}")
            
            # Store position for monitoring if needed
            if result['bracket_type'] == 'software':
                position_key = f"{symbol}_{result['main_order'].get('orderId')}"
                self.positions[position_key] = {
                    'order_id': result['main_order'].get('orderId'),
                    'symbol': symbol,
                    'side': 'BUY',
                    'quantity': quantity,
                    'entry_price': price,
                    'stop_loss_price': result['stop_loss_price'],
                    'take_profit_price': result['take_profit_price'],
                    'stop_loss_percentage': stop_loss_percentage,
                    'take_profit_percentage': take_profit_percentage,
                    'bracket_monitoring': True,
                    'created_at': asyncio.get_event_loop().time()
                }
                logger.info(f"Added bracket position for software monitoring: {position_key}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to place bracket buy order: {str(e)}")
            return None

    async def _monitor_bracket_position(self, position_key: str, position: Dict[str, Any]):
        """Monitor a bracket position for both stop-loss and take-profit triggers"""
        try:
            symbol = position['symbol']
            order_id = position['order_id']
            entry_price = position['entry_price']
            stop_loss_price = position['stop_loss_price']
            take_profit_price = position['take_profit_price']
            quantity = position['quantity']
            
            # Check if main order is still open and filled
            try:
                order_status = await self.client.get_order_status(symbol, order_id)
                status = order_status.get('status', '')
                
                if status not in ['FILLED', 'PARTIALLY_FILLED']:
                    # Order not filled yet, keep monitoring
                    return
                    
                if status == 'FILLED':
                    logger.info(f"Bracket order {order_id} filled, starting TP/SL monitoring")
                    
            except Exception as e:
                logger.warning(f"Could not check order status for {order_id}: {e}")
                return
            
            # Get current price
            ticker = await self.client.get_ticker_price(symbol)
            current_price = float(ticker.get('price', 0))
            
            if current_price <= 0:
                logger.warning(f"Invalid current price for {symbol}: {current_price}")
                return
            
            # Calculate current profit/loss percentage
            price_change_pct = ((current_price - entry_price) / entry_price) * 100
            
            logger.debug(f"Bracket monitoring {order_id}: Current ${current_price:.4f}, Entry ${entry_price:.4f}, Change: {price_change_pct:.2f}%")
            
            # Check stop-loss condition
            if current_price <= stop_loss_price:
                logger.warning(f"STOP-LOSS TRIGGERED for {order_id}!")
                logger.warning(f"  Current Price: ${current_price:.4f}")
                logger.warning(f"  Stop Loss Price: ${stop_loss_price:.4f}")
                logger.warning(f"  Loss: {price_change_pct:.2f}%")
                
                # Execute stop-loss sell
                await self._execute_bracket_exit(position_key, position, 'STOP_LOSS', current_price)
                return
            
            # Check take-profit condition
            if current_price >= take_profit_price:
                logger.info(f"TAKE-PROFIT TRIGGERED for {order_id}!")
                logger.info(f"  Current Price: ${current_price:.4f}")
                logger.info(f"  Take Profit Price: ${take_profit_price:.4f}")
                logger.info(f"  Profit: {price_change_pct:.2f}%")
                
                # Execute take-profit sell
                await self._execute_bracket_exit(position_key, position, 'TAKE_PROFIT', current_price)
                return
            
        except Exception as e:
            logger.error(f"Error monitoring bracket position {position_key}: {str(e)}")

    async def _execute_bracket_exit(self, position_key: str, position: Dict[str, Any], exit_type: str, current_price: float):
        """Execute the exit order for a bracket position"""
        try:
            symbol = position['symbol']
            quantity = position['quantity']
            entry_price = position['entry_price']
            
            logger.info(f"Executing {exit_type} for bracket position {position_key}")
            
            if self.config.dry_run:
                logger.info(f"DRY RUN: Would execute {exit_type} market sell")
                logger.info(f"  Symbol: {symbol}")
                logger.info(f"  Quantity: {quantity}")
                logger.info(f"  Current Price: ${current_price:.4f}")
                
                # Calculate profit/loss
                pnl = (current_price - entry_price) * quantity
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                
                logger.info(f"  P&L: ${pnl:.4f} ({pnl_pct:.2f}%)")
                
                # Remove from monitoring
                if position_key in self.positions:
                    del self.positions[position_key]
                return
            
            # Execute market sell order
            try:
                # FIX: Cancel take profit order FIRST to unlock quantity
                if position.get('take_profit_order_id'):
                    logger.warning(f" Cancelling take profit order {position['take_profit_order_id']} to unlock quantity for stop loss")
                    try:
                        cancel_result = await self.client.cancel_order(symbol, position['take_profit_order_id'])
                        logger.warning(f" Take profit order cancelled: {cancel_result}")
                        # Small delay to ensure cancellation is processed
                        await asyncio.sleep(0.5)
                    except Exception as cancel_e:
                        logger.error(f" Could not cancel take profit order: {cancel_e}")
                        logger.error(" QUANTITY STILL LOCKED - Stop loss may fail!")
                
                sell_result = await self.client.place_market_order(symbol, 'SELL', quantity)
                
                # Calculate actual profit/loss
                fill_price = float(sell_result.get('fills', [{}])[0].get('price', current_price))
                pnl = (fill_price - entry_price) * quantity
                pnl_pct = ((fill_price - entry_price) / entry_price) * 100
                
                logger.info(f"{exit_type} executed successfully:")
                logger.info(f"  Exit Order ID: {sell_result.get('orderId', 'Unknown')}")
                logger.info(f"  Fill Price: ${fill_price:.4f}")
                logger.info(f"  P&L: ${pnl:.4f} ({pnl_pct:.2f}%)")
                
                # Remove position from monitoring
                if position_key in self.positions:
                    del self.positions[position_key]
                    logger.info(f"Removed bracket position {position_key} from monitoring")
                
            except Exception as e:
                logger.error(f"Failed to execute {exit_type} market sell: {str(e)}")
                # Keep monitoring in case we can retry
                
        except Exception as e:
            logger.error(f"Error executing bracket exit for {position_key}: {str(e)}") 

    async def place_sequential_bracket_buy_order(
        self, 
        entry_price: float, 
        stop_loss_price: float,
        take_profit_price: float,
        quantity: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Place a sequential bracket buy order following the workflow:
        1. Place LIMIT buy order at entry_price
        2. Monitor until filled
        3. Automatically place TAKE_PROFIT and STOP_LOSS orders
        
        Args:
            entry_price: Price for the initial LIMIT buy order (e.g., 1.1 USDT)
            stop_loss_price: Price to sell if market goes down (e.g., 1.0 USDT)
            take_profit_price: Price to sell for profit (e.g., 5.0 USDT)
            quantity: Amount to buy (if None, uses configured amount)
        """
        
        if not await self.is_trading_time():
            logger.warning("Outside trading hours, skipping sequential bracket order")
            return None
        
        if not self._can_place_order():
            logger.warning("Daily order limit reached, skipping sequential bracket order")
            return None
        
        symbol = self.config.trading_params.symbol
        
        # Calculate order quantity if not provided
        if quantity is None:
            order_quantity = self._calculate_order_quantity(entry_price)
        else:
            order_quantity = quantity
        
        # Round to appropriate precision
        order_quantity = round(order_quantity, 6)
        
        # Validate prices
        if stop_loss_price >= entry_price:
            raise ValueError(f"Stop loss price ({stop_loss_price}) must be below entry price ({entry_price}) for buy orders")
        
        if take_profit_price <= entry_price:
            raise ValueError(f"Take profit price ({take_profit_price}) must be above entry price ({entry_price}) for buy orders")
        
        try:
            if self.config.dry_run:
                usdt_value = order_quantity * entry_price
                logger.info(f"DRY RUN: Sequential bracket order for {symbol}")
                logger.info(f"  1. BUY {order_quantity} @ ${entry_price} (${usdt_value:.2f} USDT)")
                logger.info(f"  2. After fill, place STOP_LOSS @ ${stop_loss_price}")
                logger.info(f"  3. After fill, place TAKE_PROFIT @ ${take_profit_price}")
                
                # Simulate the order structure
                fake_order_id = f"dry_run_seq_{int(time.time())}"
                return {
                    "main_order": {"orderId": fake_order_id, "side": "BUY", "price": entry_price, "quantity": order_quantity},
                    "bracket_type": "sequential",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss_price": stop_loss_price,
                    "take_profit_price": take_profit_price,
                    "quantity": order_quantity
                }
            
            # Place the sequential bracket order
            result = await self.client.place_sequential_bracket_order(
                symbol=symbol,
                side='BUY',
                quantity=order_quantity,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price
            )
            
            logger.info(f"Sequential bracket order initiated:")
            logger.info(f"  Main order ID: {result['main_order'].get('orderId', 'Unknown')}")
            logger.info(f"  Entry price: ${entry_price}")
            logger.info(f"  Stop loss: ${stop_loss_price}")
            logger.info(f"  Take profit: ${take_profit_price}")
            logger.info(f"  Quantity: {order_quantity}")
            
            self.daily_order_count += 1
            
            # Store position for sequential monitoring
            position_key = f"seq_{symbol}_{result['main_order'].get('orderId')}"
            self.positions[position_key] = {
                'order_id': result['main_order'].get('orderId'),
                'symbol': symbol,
                'side': 'BUY',
                'quantity': order_quantity,
                'entry_price': entry_price,
                'stop_loss_price': stop_loss_price,
                'take_profit_price': take_profit_price,
                'bracket_type': 'sequential',
                'status': 'waiting_for_fill',
                'protective_orders_placed': False,
                'stop_loss_order_id': None,
                'take_profit_order_id': None,
                'created_at': time.time()
            }
            
            logger.info(f"Added sequential bracket position for monitoring: {position_key}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to place sequential bracket order: {str(e)}")
            raise

    async def _monitor_sequential_bracket_position(self, position_key: str, position: Dict[str, Any]):
        """Monitor a sequential bracket position through its lifecycle"""
        try:
            symbol = position['symbol']
            order_id = position['order_id']
            entry_price = position['entry_price']
            stop_loss_price = position['stop_loss_price']
            take_profit_price = position['take_profit_price']
            quantity = position['quantity']
            status = position['status']
            
            # Step 1: Check if main order is filled
            if status == 'waiting_for_fill':
                try:
                    order_status = await self.client.get_order_status(symbol, order_id)
                    order_state = order_status.get('status', '')
                    
                    if order_state == 'FILLED':
                        logger.info(f" Main order {order_id} FILLED! Placing protective orders...")
                        
                        # Update position status
                        self.positions[position_key]['status'] = 'main_filled'
                        
                        # Place protective orders (STOP_LOSS and TAKE_PROFIT)
                        if not self.config.dry_run:
                            try:
                                protective_result = await self.client.place_protective_orders_after_fill(
                                    symbol=symbol,
                                    original_side='BUY',
                                    quantity=quantity,
                                    stop_loss_price=stop_loss_price,
                                    take_profit_price=take_profit_price
                                )
                                
                                # Handle software-based protection
                                if protective_result.get('software_stop_loss'):
                                    logger.info(" Software-based stop loss monitoring activated")
                                    self.positions[position_key]['software_stop_loss'] = True
                                    self.positions[position_key]['software_stop_loss_price'] = stop_loss_price
                                else:
                                    logger.info(" MEXC native stop loss protection activated")
                                    self.positions[position_key]['software_stop_loss'] = False
                                
                                # Store the protective order IDs if they exist
                                if protective_result['stop_loss_order'] and isinstance(protective_result['stop_loss_order'], dict):
                                    if 'orderId' in protective_result['stop_loss_order']:
                                        self.positions[position_key]['stop_loss_order_id'] = protective_result['stop_loss_order'].get('orderId')
                                        logger.info(f" MEXC native STOP_LOSS_LIMIT order: {protective_result['stop_loss_order'].get('orderId')}")
                                    else:
                                        logger.info(f" Software stop loss monitoring: @ {protective_result['stop_loss_order'].get('stop_price')}")
                                
                                if protective_result['take_profit_order'] and isinstance(protective_result['take_profit_order'], dict):
                                    if 'orderId' in protective_result['take_profit_order']:
                                        self.positions[position_key]['take_profit_order_id'] = protective_result['take_profit_order'].get('orderId')
                                        logger.info(f" MEXC native TAKE_PROFIT_LIMIT order: {protective_result['take_profit_order'].get('orderId')}")
                                    else:
                                        logger.info(f" Software take profit monitoring: @ {protective_result['take_profit_order'].get('target_price')}")
                                
                                self.positions[position_key]['protective_orders_placed'] = True
                                self.positions[position_key]['status'] = 'protected'
                                
                                if protective_result['errors']:
                                    logger.warning(f"Some protection setup issues: {protective_result['errors']}")
                                
                            except Exception as e:
                                logger.error(f"Failed to set up protection for {order_id}: {str(e)}")
                                # Keep monitoring, might retry later
                        else:
                            logger.info("DRY RUN: Would set up software-based stop loss and take profit monitoring")
                            self.positions[position_key]['protective_orders_placed'] = True
                            self.positions[position_key]['status'] = 'protected'
                            self.positions[position_key]['software_stop_loss'] = True
                            self.positions[position_key]['software_stop_loss_price'] = stop_loss_price
                    
                    elif order_state in ['CANCELED', 'REJECTED', 'EXPIRED']:
                        logger.warning(f"Main order {order_id} was {order_state}, removing from monitoring")
                        del self.positions[position_key]
                        
                except Exception as e:
                    logger.warning(f"Could not check order status for {order_id}: {e}")
            
            # Step 2: Monitor protective orders if they've been placed
            elif status == 'protected':
                stop_loss_order_id = position.get('stop_loss_order_id')
                take_profit_order_id = position.get('take_profit_order_id')
                software_stop_loss = position.get('software_stop_loss', False)
                
                # Check current price for software monitoring
                current_price = None
                try:
                    ticker = await self.client.get_ticker_price(symbol)
                    current_price = float(ticker.get('price', 0))
                except Exception as e:
                    logger.debug(f"Could not get current price: {e}")
                    return
                
                position_closed = False
                
                # Monitor exchange-based stop loss order
                if stop_loss_order_id and not software_stop_loss:
                    try:
                        sl_status = await self.client.get_order_status(symbol, stop_loss_order_id)
                        if sl_status.get('status') == 'FILLED':
                            logger.warning(f" MEXC STOP_LOSS_LIMIT TRIGGERED for position {position_key}")
                            logger.warning(f"  Native stop loss order {stop_loss_order_id} filled")
                            position_closed = True
                            
                            # Cancel take profit order if it exists
                            if take_profit_order_id:
                                try:
                                    await self.client.cancel_order(symbol, take_profit_order_id)
                                    logger.info(f"Cancelled take profit order {take_profit_order_id}")
                                except Exception as e:
                                    logger.warning(f"Could not cancel take profit order: {e}")
                        elif sl_status.get('status') in ['CANCELED', 'REJECTED', 'EXPIRED']:
                            logger.warning(f"⚠️ MEXC stop loss order {stop_loss_order_id} was {sl_status.get('status')}")
                            logger.warning("Switching to software monitoring for safety")
                            # Switch to software monitoring as fallback
                            position['software_stop_loss'] = True
                            position['software_stop_loss_price'] = stop_loss_price
                    except Exception as e:
                        logger.debug(f"Could not check stop loss order status: {e}")
                
                # Software-based stop loss monitoring (fallback when native orders fail)
                elif software_stop_loss and current_price:
                    software_stop_price = position.get('software_stop_loss_price', stop_loss_price)
                    
                    # Check if stop loss should trigger (for BUY positions, trigger when price drops below stop)
                    if current_price <= software_stop_price:
                        logger.warning(f" SOFTWARE STOP LOSS TRIGGERED for position {position_key}")
                        logger.warning(f"  Current price: {current_price}")
                        logger.warning(f"  Stop loss price: {software_stop_price}")
                        logger.warning("  (Using software fallback - native MEXC order failed)")
                        
                        try:
                            # FIX: Cancel take profit order FIRST to unlock quantity
                            if take_profit_order_id:
                                logger.warning(f" Cancelling take profit order {take_profit_order_id} to unlock quantity for stop loss")
                                try:
                                    cancel_result = await self.client.cancel_order(symbol, take_profit_order_id)
                                    logger.warning(f" Take profit order cancelled: {cancel_result}")
                                    # Small delay to ensure cancellation is processed
                                    await asyncio.sleep(0.5)
                                except Exception as cancel_e:
                                    logger.error(f" Could not cancel take profit order: {cancel_e}")
                                    logger.error(" QUANTITY STILL LOCKED - Stop loss may fail!")
                            
                            # Execute market sell to close position immediately
                            stop_loss_result = await self.client.place_market_order(
                                symbol=symbol,
                                side='SELL',  # Close BUY position
                                quantity=quantity
                            )
                            logger.warning(f"️ SOFTWARE stop loss executed: {stop_loss_result.get('orderId', 'Unknown')}")
                            position_closed = True
                            
                        except Exception as e:
                            error_msg = str(e)
                            logger.error(f"Failed to execute software stop loss: {error_msg}")
                            
                            # Handle "Oversold" error with ENHANCED emergency protocols
                            if "Oversold" in error_msg or "30005" in error_msg:
                                logger.error(" CRITICAL: MEXC Oversold condition preventing software stop loss!")
                                logger.error(" Activating ENHANCED EMERGENCY PROTOCOLS...")
                                
                                # Enhanced Emergency Protocol 1: Micro-batch selling
                                emergency_success = await self._execute_emergency_stop_loss_protocols(
                                    symbol, quantity, current_price, software_stop_price
                                )
                                
                                if emergency_success:
                                    logger.warning(" Emergency protocols successfully executed stop loss!")
                                    position_closed = True
                                    
                                    # Cancel take profit order if it exists
                                    if take_profit_order_id:
                                        try:
                                            await self.client.cancel_order(symbol, take_profit_order_id)
                                            logger.info(f"Cancelled take profit order {take_profit_order_id}")
                                        except Exception as e:
                                            logger.warning(f"Could not cancel take profit order: {e}")
                                else:
                                    logger.error(" ALL EMERGENCY PROTOCOLS FAILED!")
                                    logger.error(" MANUAL INTERVENTION REQUIRED!")
                            else:
                                logger.error(f"Software stop loss failed with non-oversold error: {error_msg}")
                                position_closed = True  # Prevent infinite retries
                
                # Monitor take profit order
                if take_profit_order_id and not position_closed:
                    try:
                        tp_status = await self.client.get_order_status(symbol, take_profit_order_id)
                        if tp_status.get('status') == 'FILLED':
                            logger.info(f" MEXC TAKE_PROFIT_LIMIT TRIGGERED for position {position_key}")
                            logger.info(f"  Native take profit order {take_profit_order_id} filled")
                            position_closed = True
                            
                            # Cancel stop loss order if it exists
                            if stop_loss_order_id:
                                try:
                                    await self.client.cancel_order(symbol, stop_loss_order_id)
                                    logger.info(f"Cancelled stop loss order {stop_loss_order_id}")
                                except Exception as e:
                                    logger.warning(f"Could not cancel stop loss order: {e}")
                        elif tp_status.get('status') in ['CANCELED', 'REJECTED', 'EXPIRED']:
                            logger.warning(f"⚠️ MEXC take profit order {take_profit_order_id} was {tp_status.get('status')}")
                            # Take profit order failed, but keep stop loss active
                    except Exception as e:
                        logger.debug(f"Could not check take profit order status: {e}")
                
                # Software-based take profit monitoring (fallback when native order fails or doesn't exist)
                if not take_profit_order_id and current_price and not position_closed:
                    # Check if current price reaches take profit level
                    if current_price >= take_profit_price:
                        logger.info(f" SOFTWARE TAKE PROFIT TRIGGERED for position {position_key}")
                        logger.info(f"  Current price: {current_price}")
                        logger.info(f"  Take profit price: {take_profit_price}")
                        logger.info("  (Using software fallback - native MEXC order not available)")
                        
                        try:
                            # Execute market sell to take profit
                            take_profit_result = await self.client.place_market_order(
                                symbol=symbol,
                                side='SELL',  # Close BUY position
                                quantity=quantity
                            )
                            logger.info(f" SOFTWARE take profit executed: {take_profit_result.get('orderId', 'Unknown')}")
                            position_closed = True
                            
                            # Cancel stop loss order if it exists
                            if stop_loss_order_id:
                                try:
                                    await self.client.cancel_order(symbol, stop_loss_order_id)
                                    logger.info(f"Cancelled stop loss order {stop_loss_order_id}")
                                except Exception as e:
                                    logger.warning(f"Could not cancel stop loss order: {e}")
                        except Exception as e:
                            logger.error(f"Failed to execute software take profit: {str(e)}")
                            # Don't mark as closed for take profit failures
                
                # Remove position if closed
                if position_closed:
                    logger.info(f"Sequential bracket position {position_key} completed, removing from monitoring")
                    del self.positions[position_key]
            
        except Exception as e:
            logger.error(f"Error monitoring sequential bracket position {position_key}: {str(e)}") 

    async def place_simple_bracket_order(
        self, 
        entry_price: float, 
        stop_loss_price: float,
        take_profit_price: float,
        quantity: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Simple bracket order using MEXC's native SL/TP capabilities
        
        Places a single LIMIT order with built-in stop loss and take profit.
        No complex monitoring needed - MEXC should handle everything automatically!
        
        Args:
            entry_price: Price for the initial LIMIT buy order
            stop_loss_price: Price to trigger stop loss
            take_profit_price: Price to trigger take profit  
            quantity: Amount to buy (if None, uses configured amount)
        """
        
        if not await self.is_trading_time():
            logger.warning("Outside trading hours, skipping bracket order")
            return None
        
        if not self._can_place_order():
            logger.warning("Daily order limit reached, skipping bracket order")
            return None
        
        symbol = self.config.trading_params.symbol
        
        # Calculate order quantity if not provided
        if quantity is None:
            order_quantity = self._calculate_order_quantity(entry_price)
        else:
            order_quantity = quantity
        
        # Round to appropriate precision
        order_quantity = round(order_quantity, 6)
        
        # Validate prices
        if stop_loss_price >= entry_price:
            raise ValueError(f"Stop loss price ({stop_loss_price}) must be below entry price ({entry_price}) for buy orders")
        
        if take_profit_price <= entry_price:
            raise ValueError(f"Take profit price ({take_profit_price}) must be above entry price ({entry_price}) for buy orders")
        
        try:
            if self.config.dry_run:
                usdt_value = order_quantity * entry_price
                logger.info(f"DRY RUN: Simple bracket order for {symbol}")
                logger.info(f"  BUY {order_quantity} @ ${entry_price} (${usdt_value:.2f} USDT)")
                logger.info(f"  Stop Loss: ${stop_loss_price}")
                logger.info(f"  Take Profit: ${take_profit_price}")
                logger.info("  All SL/TP handled by MEXC exchange automatically")
                
                return {
                    "bracket_type": "dry_run_native",
                    "main_order": {"orderId": f"dry_run_{int(time.time())}", "side": "BUY", "price": entry_price, "quantity": order_quantity},
                    "entry_price": entry_price,
                    "stop_loss_price": stop_loss_price,
                    "take_profit_price": take_profit_price,
                    "quantity": order_quantity
                }
            
            # Use MEXC's native bracket order functionality
            result = await self.client.place_bracket_limit_order(
                symbol=symbol,
                side='BUY',
                quantity=order_quantity,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price
            )
            
            logger.info(f"   Native bracket order placed successfully!")
            logger.info(f"  Bracket Type: {result['bracket_type']}")
            logger.info(f"  Entry Price: ${entry_price}")
            logger.info(f"  Stop Loss: ${stop_loss_price}")
            logger.info(f"  Take Profit: ${take_profit_price}")
            logger.info(f"  Quantity: {order_quantity}")
            logger.info("    MEXC will handle all SL/TP execution automatically")
            
            self.daily_order_count += 1
            return result
            
        except Exception as e:
            logger.error(f"Failed to place simple bracket order: {str(e)}")
            raise 

    async def _execute_emergency_stop_loss_protocols(self, symbol: str, quantity: float, current_price: float, software_stop_price: float) -> bool:
        """Execute emergency stop loss protocols"""
        try:
            # CRITICAL: First check if we need to cancel take profit orders to unlock quantity
            # This is essential because quantity might be locked in take profit orders
            logger.warning(" Checking for locked quantity in take profit orders...")
            
            # Look for active take profit orders for this symbol
            try:
                open_orders = await self.client.get_open_orders(symbol)
                take_profit_orders = [order for order in open_orders if order.get('side') == 'SELL']
                
                if take_profit_orders:
                    logger.warning(f" Found {len(take_profit_orders)} active SELL orders that may be locking quantity")
                    
                    for tp_order in take_profit_orders:
                        order_id = tp_order.get('orderId')
                        order_qty = float(tp_order.get('origQty', 0))
                        logger.warning(f" Cancelling SELL order {order_id} (qty: {order_qty}) to unlock quantity")
                        
                        try:
                            await self.client.cancel_order(symbol, order_id)
                            logger.warning(f" Cancelled order {order_id}")
                        except Exception as cancel_e:
                            logger.error(f" Could not cancel order {order_id}: {cancel_e}")
                    
                    # Wait for cancellations to process
                    await asyncio.sleep(1.0)
                    logger.warning(" Quantity unlock complete, proceeding with emergency protocols")
                else:
                    logger.info(" No active SELL orders found, quantity should be available")
                    
            except Exception as orders_e:
                logger.warning(f"Could not check open orders: {orders_e}")
                logger.warning(" Proceeding with emergency protocols anyway")
            
            # Enhanced Emergency Protocol 1: Micro-batch selling
            micro_batch_success = await self._execute_micro_batch_selling(symbol, quantity, current_price, software_stop_price)
            
            if micro_batch_success:
                return True
            
            # Enhanced Emergency Protocol 2: Discounted LIMIT order
            discount_success = await self._execute_limit_order_with_discount(symbol, quantity, current_price, software_stop_price)
            
            if discount_success:
                return True
            
            # Enhanced Emergency Protocol 3: Progressive retry strategy
            retry_success = await self._execute_progressive_retry_strategy(symbol, quantity, current_price, software_stop_price)
            
            return retry_success
            
        except Exception as e:
            logger.error(f"Failed to execute emergency stop loss protocols: {str(e)}")
            return False

    async def _execute_micro_batch_selling(self, symbol: str, quantity: float, current_price: float, software_stop_price: float) -> bool:
        """Execute micro-batch selling strategy to bypass MEXC Oversold condition"""
        logger.warning(" EMERGENCY PROTOCOL 1: Micro-batch selling strategy")
        
        try:
            # Get proper quantity formatting
            try:
                precision_info = await self.client.get_symbol_precision_info(symbol)
                step_size = precision_info.get('stepSize', '0.1')
            except Exception as e:
                logger.warning(f"Could not get precision info, using default: {e}")
                step_size = '0.1'  # Conservative fallback
            
            # Calculate micro-batch sizes (start small, increase gradually)
            micro_batches = []
            remaining_quantity = quantity
            
            # Create progressively larger batches
            batch_sizes = [0.5, 0.8, 1.0, 1.5, 2.0]  # XRP amounts
            
            for batch_size in batch_sizes:
                if remaining_quantity > 0:
                    actual_batch = min(batch_size, remaining_quantity)
                    formatted_batch = self.client.format_quantity(actual_batch, step_size)
                    micro_batches.append(formatted_batch)
                    remaining_quantity -= formatted_batch
                    
                    if remaining_quantity <= 0.05:  # Stop if very small amount left
                        break
            
            # Add any remaining quantity as final batch
            if remaining_quantity > 0.05:
                final_batch = self.client.format_quantity(remaining_quantity, step_size)
                micro_batches.append(final_batch)
            
            logger.info(f" Created {len(micro_batches)} micro-batches: {micro_batches}")
            
            total_sold = 0
            successful_batches = 0
            
            for i, batch_quantity in enumerate(micro_batches):
                try:
                    logger.info(f" Attempting micro-batch {i+1}/{len(micro_batches)}: {batch_quantity} {symbol}")
                    
                    # Try market order first
                    result = await self.client.place_market_order(
                        symbol=symbol,
                        side='SELL',
                        quantity=batch_quantity
                    )
                    
                    logger.info(f" Micro-batch {i+1} successful: {result.get('orderId', 'Unknown')}")
                    total_sold += batch_quantity
                    successful_batches += 1
                    
                    # Small delay between batches to avoid rate limiting
                    await asyncio.sleep(0.2)
                    
                except Exception as batch_e:
                    error_msg = str(batch_e)
                    logger.warning(f" Micro-batch {i+1} failed: {error_msg}")
                    
                    if "Oversold" not in error_msg and "30005" not in error_msg:
                        # If it's not oversold error, this strategy might work for remaining batches
                        continue
                    else:
                        # Still oversold, stop trying micro-batches
                        logger.warning(" Micro-batches still blocked by Oversold condition")
                        break
            
            if total_sold > 0:
                logger.warning(f" Micro-batch strategy partially successful: {total_sold}/{quantity} sold ({successful_batches} batches)")
                
                # If we sold most of the position, consider it a success
                if total_sold >= quantity * 0.8:  # 80% or more sold
                    logger.warning(" Micro-batch strategy achieved significant position reduction!")
                    return True
                else:
                    logger.warning(" Micro-batch strategy only achieved partial position reduction")
                    return False
            else:
                logger.warning(" Micro-batch strategy failed - no batches executed")
                return False
                
        except Exception as e:
            logger.error(f"Micro-batch selling strategy failed: {str(e)}")
            return False

    async def _execute_limit_order_with_discount(self, symbol: str, quantity: float, current_price: float, software_stop_price: float) -> bool:
        """Execute discounted LIMIT orders to bypass MEXC Oversold condition"""
        logger.warning(" EMERGENCY PROTOCOL 2: Discounted LIMIT order strategy")
        
        try:
            # Get proper quantity formatting
            try:
                precision_info = await self.client.get_symbol_precision_info(symbol)
                step_size = precision_info.get('stepSize', '0.1')
                formatted_quantity = self.client.format_quantity(quantity, step_size)
            except Exception as e:
                logger.warning(f"Could not get precision info, using default: {e}")
                formatted_quantity = round(quantity, 1)
            
            # Try progressively deeper discounts
            discount_levels = [
                ("Conservative", 0.5),   # 0.5% below market
                ("Moderate", 1.0),       # 1.0% below market  
                ("Aggressive", 2.0),     # 2.0% below market
                ("Desperate", 3.0)       # 3.0% below market
            ]
            
            for discount_name, discount_percent in discount_levels:
                try:
                    # Calculate discounted price
                    discount_price = current_price * (1 - discount_percent / 100)
                    
                    logger.info(f" Trying {discount_name} LIMIT order: {formatted_quantity} @ ${discount_price:.6f} ({discount_percent}% discount)")
                    
                    result = await self.client.place_limit_order(
                        symbol=symbol,
                        side='SELL',
                        quantity=formatted_quantity,
                        price=discount_price
                    )
                    
                    logger.warning(f" {discount_name} LIMIT order placed: {result.get('orderId', 'Unknown')}")
                    logger.warning(f" Order should fill quickly at {discount_percent}% discount")
                    
                    # Wait a moment to see if it fills quickly
                    await asyncio.sleep(1.0)
                    
                    # Check if order filled
                    try:
                        order_status = await self.client.get_order_status(symbol, result.get('orderId'))
                        if order_status.get('status') == 'FILLED':
                            logger.warning(f" {discount_name} LIMIT order FILLED immediately!")
                            return True
                        else:
                            logger.info(f" {discount_name} LIMIT order placed, status: {order_status.get('status', 'Unknown')}")
                            # Leave the order active and try deeper discount
                    except Exception as status_e:
                        logger.debug(f"Could not check order status: {status_e}")
                    
                    # Don't cancel immediately - let it sit in the order book
                    # If price drops further, it should fill
                    
                except Exception as limit_e:
                    error_msg = str(limit_e)
                    logger.warning(f" {discount_name} LIMIT order failed: {error_msg}")
                    
                    if "Oversold" in error_msg or "30005" in error_msg:
                        logger.warning(f" Even {discount_name} LIMIT orders blocked by Oversold")
                        continue
                    else:
                        # Different error, might be solvable
                        continue
            
            logger.warning(" Discounted LIMIT orders strategy completed")
            logger.warning(" Orders are active in order book, should fill if price drops further")
            return True  # Consider success if we placed orders
            
        except Exception as e:
            logger.error(f"Discounted LIMIT order strategy failed: {str(e)}")
            return False

    async def _execute_progressive_retry_strategy(self, symbol: str, quantity: float, current_price: float, software_stop_price: float) -> bool:
        """Execute progressive retry strategy with intelligent waiting"""
        logger.warning(" EMERGENCY PROTOCOL 3: Progressive retry strategy")
        
        try:
            # Get proper quantity formatting
            try:
                precision_info = await self.client.get_symbol_precision_info(symbol)
                step_size = precision_info.get('stepSize', '0.1')
                formatted_quantity = self.client.format_quantity(quantity, step_size)
            except Exception as e:
                logger.warning(f"Could not get precision info, using default: {e}")
                formatted_quantity = round(quantity, 1)
            
            # Progressive retry with increasing delays and minimum order testing
            retry_strategies = [
                ("Quick retry", 0.5, "minimum"),     # 0.5s delay, minimum order
                ("Medium retry", 2.0, "partial"),    # 2s delay, partial quantity
                ("Patient retry", 5.0, "full"),      # 5s delay, full quantity
                ("Final attempt", 10.0, "full")      # 10s delay, final try
            ]
            
            for strategy_name, delay, order_type in retry_strategies:
                try:
                    logger.info(f" {strategy_name}: waiting {delay}s for MEXC condition to clear...")
                    await asyncio.sleep(delay)
                    
                    # Determine quantity based on strategy
                    if order_type == "minimum":
                        # Try minimum viable order (0.1 XRP) to test if MEXC unblocked
                        test_quantity = self.client.format_quantity(0.1, step_size)
                        logger.info(f" Testing with minimum order: {test_quantity}")
                    elif order_type == "partial":
                        # Try half quantity
                        test_quantity = self.client.format_quantity(formatted_quantity / 2, step_size)
                        logger.info(f" Testing with partial order: {test_quantity}")
                    else:
                        # Try full quantity
                        test_quantity = formatted_quantity
                        logger.info(f" Testing with full order: {test_quantity}")
                    
                    # Get current price (might have changed)
                    try:
                        ticker = await self.client.get_ticker_price(symbol)
                        current_price = float(ticker.get('price', current_price))
                    except:
                        pass  # Use existing price if update fails
                    
                    # Try market order
                    result = await self.client.place_market_order(
                        symbol=symbol,
                        side='SELL',
                        quantity=test_quantity
                    )
                    
                    logger.warning(f" {strategy_name} successful: {result.get('orderId', 'Unknown')}")
                    logger.warning(f" MEXC Oversold condition has cleared!")
                    
                    # If this was a test order, place the remaining quantity
                    if test_quantity < formatted_quantity:
                        remaining_quantity = formatted_quantity - test_quantity
                        logger.info(f" Placing remaining quantity: {remaining_quantity}")
                        
                        try:
                            remaining_result = await self.client.place_market_order(
                                symbol=symbol,
                                side='SELL',
                                quantity=remaining_quantity
                            )
                            logger.warning(f" Remaining quantity sold: {remaining_result.get('orderId', 'Unknown')}")
                        except Exception as remaining_e:
                            logger.warning(f" Could not sell remaining quantity: {remaining_e}")
                            logger.warning(f" But partial exit successful: {test_quantity}/{formatted_quantity}")
                    
                    return True
                    
                except Exception as retry_e:
                    error_msg = str(retry_e)
                    logger.warning(f" {strategy_name} failed: {error_msg}")
                    
                    if "Oversold" not in error_msg and "30005" not in error_msg:
                        logger.warning(f" MEXC unblocked but got different error: {error_msg}")
                        return False  # Stop trying, different issue
                    else:
                        # Still oversold, continue to next strategy
                        continue
            
            logger.error(" Progressive retry strategy exhausted - MEXC Oversold condition persists")
            return False
            
        except Exception as e:
            logger.error(f"Progressive retry strategy failed: {str(e)}")
            return False 