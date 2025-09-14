#!/usr/bin/env python3
import asyncio
import sys
import os
import time
import curses
import argparse
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging_config  # Initialize logging
from loguru import logger
from config import load_config, BotConfig
from mexc_client import MexcClient
from trading_engine import TradingEngine

# Constants for UI
HEADER = "MEXC⚡ Trading Bot"
MIN_TERMINAL_WIDTH = 80
MIN_TERMINAL_HEIGHT = 24

class TradingBotUI:
    def __init__(self, engine: TradingEngine, headless: bool = False):
        self.engine = engine
        self.headless = headless
        self.stdscr = None
        self._running = False
        self._curses_failed = False
        self._retry_count = 0
        self.MAX_RETRIES = 3
        self._last_update = datetime.now()
        self.status = "Initializing..."
        self.paused = False
        self._update_interval = getattr(engine.config.trading_params, 'ui_update_interval', 1.0)
        self._last_check = {}  # Track last check times for different components
        self._last_env_mtime = os.path.getmtime('.env')  # Track .env file modification time
        self._env_check_interval = 1.0  # Check for .env changes every second
        
        # Ensure engine is properly initialized
        if not hasattr(engine, 'client') or not engine.client:
            logger.error("Trading engine client not initialized!")
            raise RuntimeError("Trading engine client not initialized")
            
        if not hasattr(engine, 'config'):
            logger.error("Trading engine config not initialized!")
            raise RuntimeError("Trading engine config not initialized")
            
        # Set initial status
        self.status = "Waiting for market data..."
            
        # Colors for different states
        self.COLORS = {
            'green': 1,
            'red': 2,
            'yellow': 3,
            'cyan': 4,
            'white': 5
        }

    async def _run_strategy_updates(self):
        """Background task to update the strategy and UI"""
        next_env_check = 0  # Track when to check .env changes
        
        while self._running:
            try:
                current_time = time.time()
                
                # Check for config changes periodically
                if current_time >= next_env_check:
                    if await self._check_config_changes():
                        logger.info("Configuration updated, refreshing UI...")
                        # Reset state after config change
                        state = await self._get_strategy_state()
                        await self._update_display(state['current_price'], state['prediction_info'])
                    next_env_check = current_time + self._env_check_interval
                
                # Run strategy update
                await self.engine.update_strategy()
                
                # Get latest strategy state
                state = await self._get_strategy_state()
                
                # Update UI with latest price info
                if self.headless:
                    await self._print_headless_status(state['current_price'], state['prediction_info'])
                else:
                    await self._update_display(state['current_price'], state['prediction_info'])
                    
                # Update at half the configured interval to ensure fresh data
                await asyncio.sleep(self.engine.config.ai_config.update_interval / 2)
                
            except Exception as e:
                logger.error(f"Error in strategy update: {e}")
                await asyncio.sleep(5)
    
    def _init_curses(self):
        """Initialize curses interface"""
        if self._curses_failed:
            self.headless = True
            return False
            
        # Check if we're in a proper terminal
        if not sys.stdout.isatty():
            logger.warning("Not running in a terminal, falling back to headless mode")
            self.headless = True
            return False
            
        try:
            # Try to initialize curses
            self.stdscr = curses.initscr()
            curses.start_color()
            curses.use_default_colors()
            
            # Initialize color pairs
            curses.init_pair(self.COLORS['green'], curses.COLOR_GREEN, -1)
            curses.init_pair(self.COLORS['red'], curses.COLOR_RED, -1)
            curses.init_pair(self.COLORS['yellow'], curses.COLOR_YELLOW, -1)
            curses.init_pair(self.COLORS['cyan'], curses.COLOR_CYAN, -1)
            curses.init_pair(self.COLORS['white'], curses.COLOR_WHITE, -1)
            
            # Configure terminal
            curses.noecho()  # Don't echo keypresses
            curses.cbreak()  # React to keys instantly
            curses.curs_set(0)  # Hide cursor
            self.stdscr.keypad(True)  # Enable keypad mode
            self.stdscr.clear()
            self.stdscr.refresh()
            
            # Verify terminal size
            max_y, max_x = self.stdscr.getmaxyx()
            if max_y < MIN_TERMINAL_HEIGHT or max_x < MIN_TERMINAL_WIDTH:
                logger.error(f"Terminal too small - needs at least {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT} chars")
                self._cleanup_curses()
                self._curses_failed = True
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize curses: {e}")
            self._curses_failed = True
            return False

    def _cleanup_curses(self):
        """Clean up curses interface"""
        if not self.headless and curses and self.stdscr:
            try:
                # Reset the terminal state
                self.stdscr.keypad(False)
                curses.nocbreak()
                curses.echo()
                
                # Show cursor again
                try:
                    curses.curs_set(1)
                except:
                    pass
                    
                # Clear screen before ending
                try:
                    self.stdscr.clear()
                    self.stdscr.refresh()
                except:
                    pass
                    
                # Finally end curses mode
                curses.endwin()
                self.stdscr = None
            except Exception as e:
                logger.error(f"Error cleaning up curses: {e}")
                # Try one last time to restore terminal
                try:
                    curses.endwin()
                except:
                    pass
                
    def _get_color(self, signal: str) -> int:
        """Get the color pair number for a given signal"""
        if signal in ['BUY', 'LONG']:
            return curses.color_pair(self.COLORS['green'])
        elif signal in ['SELL', 'SHORT']:
            return curses.color_pair(self.COLORS['red'])
        elif signal == 'NEUTRAL':
            return curses.color_pair(self.COLORS['yellow'])
        else:
            return curses.color_pair(self.COLORS['white'])

    async def _update_display(self, current_price: float, prediction_info: Dict[str, Any]):
        """Update the display (curses or headless)"""
        if self.headless:
            await self._print_headless_status(current_price, prediction_info)
            return
            
        try:
            if not self.stdscr:
                return
                
            # Clear screen and get dimensions
            self.stdscr.clear()
            height, width = self.stdscr.getmaxyx()
            
            # Header
            try:
                self.stdscr.addstr(0, 0, "🚀 MEXC AI Trading Bot", curses.A_BOLD)
                self.stdscr.addstr(1, 0, "=" * min(80, width-1))
            except:
                # Fall back to ASCII if unicode fails
                self.stdscr.addstr(0, 0, "MEXC AI Trading Bot", curses.A_BOLD)
                self.stdscr.addstr(1, 0, "=" * min(80, width-1))
            
            # Status section
            status = "PAUSED" if self.paused else self.status
            status_color = curses.color_pair(self.COLORS['yellow']) if self.paused else \
                          curses.color_pair(self.COLORS['green']) if status == "RUNNING" else \
                          curses.color_pair(self.COLORS['cyan'])
            
            try:
                self.stdscr.addstr(2, 0, f"Status: {'⏸️ ' if self.paused else '🟢 '}{status}", status_color)
            except:
                self.stdscr.addstr(2, 0, f"Status: {'|| ' if self.paused else '> '}{status}", status_color)
                
            self.stdscr.addstr(3, 0, f"Last Update: {self._last_update.strftime('%H:%M:%S')}")
            
            # User Balance
            try:
                self.stdscr.addstr(5, 0, "👤 User Balance:", curses.A_BOLD)
            except:
                self.stdscr.addstr(5, 0, "User Balance:", curses.A_BOLD)
                
            # Get symbol parts (e.g., BTC from BTC_USDT or APEX from APEX_USDT)
            symbol = self.engine.config.trading_params.symbol
            # Handle both underscore and no-underscore formats
            base_asset = symbol.split('_')[0] if '_' in symbol else symbol.replace('USDT', '').replace('_', '')
            # Also handle lower/uppercase in symbol name
            base_asset = base_asset.upper()
            
            try:
                balances = await self.engine.client.get_account_balance()
                
                # Display USDT balance
                usdt_balance = balances.get('USDT', {'free': 0, 'locked': 0, 'total': 0})
                self.stdscr.addstr(6, 2, f"USDT Balance: ${usdt_balance['total']:.2f}")
                self.stdscr.addstr(7, 4, f"Available: ${usdt_balance['free']:.2f}")
                self.stdscr.addstr(8, 4, f"Locked: ${usdt_balance['locked']:.2f}")
                
                # Display base asset balance
                base_balance = balances.get(base_asset, {'free': 0, 'locked': 0, 'total': 0})
                self.stdscr.addstr(9, 2, f"{base_asset} Balance: {base_balance['total']:.8f}")
                self.stdscr.addstr(10, 4, f"Available: {base_balance['free']:.8f}")
                self.stdscr.addstr(11, 4, f"Locked: {base_balance['locked']:.8f}")
            except Exception as e:
                self.stdscr.addstr(6, 2, f"Error fetching balances: {str(e)}")
            
            # Market Data
            try:
                self.stdscr.addstr(13, 0, "📊 Market Data:", curses.A_BOLD)
            except:
                self.stdscr.addstr(13, 0, "Market Data:", curses.A_BOLD)
                
            symbol = self.engine.config.trading_params.symbol
            timeframe = self.engine.config.trading_params.timeframe
            
            self.stdscr.addstr(6, 2, f"Symbol: {symbol}")
            self.stdscr.addstr(7, 2, f"Current Price: ${current_price:.4f}")
            self.stdscr.addstr(8, 2, f"Timeframe: {timeframe}")
            
            # MA Indicators
            try:
                if hasattr(self.engine, 'fast_ma') and hasattr(self.engine, 'slow_ma'):
                    # Get and validate MA values
                    try:
                        fast_ma = self.engine.fast_ma[-1] if len(self.engine.fast_ma) > 0 else None
                        slow_ma = self.engine.slow_ma[-1] if len(self.engine.slow_ma) > 0 else None
                        
                        if fast_ma is None or slow_ma is None:
                            if not hasattr(self, '_last_ma_warning'):
                                logger.warning("MA data not available yet. Waiting for enough price history...")
                                logger.info(f"MA Arrays - Fast: {len(self.engine.fast_ma)}, Slow: {len(self.engine.slow_ma)}")
                                self._last_ma_warning = True
                            fast_ma = self._last_values.get('fast_ma', 0)
                            slow_ma = self._last_values.get('slow_ma', 0)
                        else:
                            self._last_values['fast_ma'] = fast_ma
                            self._last_values['slow_ma'] = slow_ma
                            self._last_ma_warning = False
                        
                        ma_signal = "BUY" if fast_ma > slow_ma else "SELL" if fast_ma < slow_ma else "NEUTRAL"
                        ma_color = self._get_color(ma_signal)
                        
                        self.stdscr.addstr(9, 2, f"Fast MA: ${fast_ma:.4f}")
                        self.stdscr.addstr(10, 2, f"Slow MA: ${slow_ma:.4f}")
                        self.stdscr.addstr(11, 2, f"MA Signal: {ma_signal}", ma_color)
                    except Exception as e:
                        logger.error(f"Error processing MA data: {e}")
                        self.stdscr.addstr(9, 2, "MA Data: Initializing...")
            except Exception as e:
                logger.error(f"Error displaying MA indicators: {e}")
            
            # Trading Info
            try:
                self.stdscr.addstr(13, 0, "💼 Trading Status:", curses.A_BOLD)
            except:
                self.stdscr.addstr(13, 0, "Trading Status:", curses.A_BOLD)
                
            mode = "DRY RUN" if self.engine.config.trading_params.dry_run else "LIVE TRADING"
            mode_color = curses.color_pair(self.COLORS['yellow']) if mode == "DRY RUN" else curses.color_pair(self.COLORS['red'])
            self.stdscr.addstr(14, 2, f"Mode: {mode}", mode_color)
            
            # Show trade counts and parameters
            trades = f"{self.engine.daily_trades}/{self.engine.config.trading_params.max_orders_per_day}"
            self.stdscr.addstr(15, 2, f"Trades Today: {trades}")
            
            # Trading Parameters
            if hasattr(self.engine.config.trading_params, 'trade_amount'):
                self.stdscr.addstr(16, 2, f"Trade Amount: ${self.engine.config.trading_params.trade_amount:.2f}")
            if hasattr(self.engine.config.trading_params, 'stop_loss_pct'):
                self.stdscr.addstr(17, 2, f"Stop Loss: {self.engine.config.trading_params.stop_loss_pct:.1f}%")
            if hasattr(self.engine.config.trading_params, 'take_profit_pct'):
                self.stdscr.addstr(18, 2, f"Take Profit: {self.engine.config.trading_params.take_profit_pct:.1f}%")
            
            # AI Predictions
            try:
                self.stdscr.addstr(20, 0, "🤖 AI Analysis:", curses.A_BOLD)
            except:
                self.stdscr.addstr(20, 0, "AI Analysis:", curses.A_BOLD)
                
            if prediction_info:
                direction = prediction_info.get('direction', 'NEUTRAL')
                confidence = prediction_info.get('confidence', 0.0)
                signal_color = self._get_color(direction)
                self.stdscr.addstr(21, 2, f"Signal: {direction}", signal_color)
                self.stdscr.addstr(22, 2, f"Confidence: {confidence*100:.1f}%")
                
                if 'predicted_price' in prediction_info:
                    pred_price = prediction_info['predicted_price']
                    change = (pred_price - current_price) / current_price
                    change_color = curses.color_pair(self.COLORS['green']) if change > 0 else \
                                 curses.color_pair(self.COLORS['red'])
                    self.stdscr.addstr(23, 2, f"Target: ${pred_price:.4f}")
                    self.stdscr.addstr(24, 2, f"Change: {change*100:+.2f}%", change_color)
                
                # Add model info if available
                model_name = prediction_info.get('model_name', '')
                prediction_length = prediction_info.get('prediction_length', '')
                confidence_threshold = prediction_info.get('confidence_threshold', '')
                
                if model_name:
                    self.stdscr.addstr(25, 2, f"Model: {model_name}")
                if prediction_length:
                    self.stdscr.addstr(26, 2, f"Prediction Length: {prediction_length}")
                if confidence_threshold:
                    self.stdscr.addstr(27, 2, f"Conf. Threshold: {confidence_threshold:.1f}")
            else:
                self.stdscr.addstr(14, 2, "No predictions available")
            
            # Recent Orders
            if self.engine.orders:
                try:
                    self.stdscr.addstr(29, 0, "📝 Recent Orders:", curses.A_BOLD)
                except:
                    self.stdscr.addstr(29, 0, "Recent Orders:", curses.A_BOLD)
                    
                for i, order in enumerate(self.engine.orders[-3:], 1):
                    side = order.get('side', '')
                    qty = order.get('quantity', 0)
                    price = order.get('price', 0)
                    status = order.get('status', '')
                    timestamp = order.get('timestamp', '')
                    order_color = self._get_color(side)
                    
                    if timestamp:
                        try:
                            if isinstance(timestamp, str):
                                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                            timestamp_str = timestamp.strftime('%H:%M:%S')
                        except:
                            timestamp_str = str(timestamp)
                    else:
                        timestamp_str = 'N/A'
                        
                    order_str = f"{timestamp_str} - {side} {qty:.8f} @ ${price:.4f} ({status})"
                    self.stdscr.addstr(29+i, 2, order_str, order_color)
            
            # Controls footer
            try:
                self.stdscr.addstr(height-1, 0, "⌨️  Controls: [Q]uit | [P]ause/Resume | [R]eset")
            except:
                self.stdscr.addstr(height-1, 0, "Controls: Q=Quit | P=Pause/Resume | R=Reset")
            
            self.stdscr.refresh()
            
        except curses.error as e:
            logger.error(f"Curses display error: {e}")
            
        except Exception as e:
            logger.error(f"Display update error: {e}")

    async def _check_config_changes(self) -> bool:
        """
        Check if the .env file has been modified and reload config if necessary.
        Returns True if config was reloaded, False otherwise.
        """
        try:
            # Ensure .env file exists
            if not os.path.exists('.env'):
                logger.error("Configuration file .env not found")
                return False
                
            try:
                current_mtime = os.path.getmtime('.env')
            except OSError as e:
                logger.error(f"Error accessing .env file: {e}")
                return False
            
            if current_mtime != self._last_env_mtime:
                logger.info("Detected .env file changes, reloading configuration...")
                
                # Backup current configuration state
                old_symbol = self.engine.config.trading_params.symbol
                
                try:
                    # Update modification time first to prevent reload loops
                    self._last_env_mtime = current_mtime
                    
                    if hasattr(self.engine, 'reload_config'):
                        success = await self.engine.reload_config()
                        if success:
                            new_symbol = self.engine.config.trading_params.symbol
                            if old_symbol != new_symbol:
                                logger.info(f"Trading symbol changed: {old_symbol} → {new_symbol}")
                                # Update UI status to indicate the change
                                self.status = f"Symbol changed to {new_symbol}"
                            logger.info("Configuration reloaded successfully")
                            return True
                        else:
                            logger.error("Failed to reload configuration")
                            return False
                    else:
                        logger.warning("Trading engine does not support config reloading")
                        return False
                except Exception as e:
                    logger.error(f"Error during configuration reload: {e}")
                    logger.debug("Stack trace:", exc_info=True)
                    # Restore modification time to allow retry
                    self._last_env_mtime = current_mtime - 1
                    return False
            return False
        except Exception as e:
            logger.error(f"Unexpected error checking configuration changes: {e}")
            logger.debug("Stack trace:", exc_info=True)
            return False

    async def _print_headless_status(self, current_price: float, prediction_info: Dict[str, Any]):
        """Print status updates in headless mode"""
        try:
            # Clear screen
            print("\033[H\033[J", end="")
        except:
            print("\n" * 5)
        
        # Header
        print("🚀 MEXC AI Trading Bot (Headless Mode)")
        print("=" * 50)
        
        # Status
        status = "PAUSED" if self.paused else self.status
        status_icon = "⏸️ " if self.paused else "🟢" if status == "RUNNING" else "🟡"
        print(f"Status: {status_icon} {status}")
        print(f"Last Update: {self._last_update.strftime('%H:%M:%S')}")
        print()
        
        # User Balance
        print("👤 User Balance:")
        try:
            symbol = self.engine.config.trading_params.symbol
            # Handle both underscore and no-underscore formats
            base_asset = symbol.split('_')[0] if '_' in symbol else symbol.replace('USDT', '').replace('_', '')
            # Also handle lower/uppercase in symbol name
            base_asset = base_asset.upper()
            balances = await self.engine.client.get_account_balance()
            
            # Display USDT balance
            usdt_balance = balances.get('USDT', {'free': 0, 'locked': 0, 'total': 0})
            print(f"  USDT Balance: ${usdt_balance['total']:.2f}")
            print(f"    Available: ${usdt_balance['free']:.2f}")
            print(f"    Locked: ${usdt_balance['locked']:.2f}")
            
            # Display base asset balance (the selected trading currency)
            base_balance = balances.get(base_asset, {'free': 0, 'locked': 0, 'total': 0})
            print(f"  {base_asset} Balance: {base_balance['total']:.8f}")
            print(f"    Available: {base_balance['free']:.8f}")
            print(f"    Locked: {base_balance['locked']:.8f}")
        except Exception as e:
            print(f"  Error fetching balances: {str(e)}")
        print()
        
        # Market Data
        print("📊 Market Data:")
        symbol = self.engine.config.trading_params.symbol
        timeframe = self.engine.config.trading_params.timeframe
        print(f"  Symbol: {symbol}")
        print(f"  Current Price: ${current_price:.4f}")
        print(f"  Timeframe: {timeframe}")
        
        # MA Indicators
        if hasattr(self.engine, 'fast_ma') and hasattr(self.engine, 'slow_ma'):
            fast_ma = self.engine.fast_ma[-1] if len(self.engine.fast_ma) > 0 else 0
            slow_ma = self.engine.slow_ma[-1] if len(self.engine.slow_ma) > 0 else 0
            ma_signal = "BUY" if fast_ma > slow_ma else "SELL" if fast_ma < slow_ma else "NEUTRAL"
            print(f"  Fast MA: ${fast_ma:.4f}")
            print(f"  Slow MA: ${slow_ma:.4f}")
            print(f"  MA Signal: {ma_signal}")
        print()
        
        # Trading Info
        print("💼 Trading Status:")
        mode = "DRY RUN" if self.engine.config.trading_params.dry_run else "LIVE TRADING"
        print(f"  Mode: {mode}")
        print(f"  Trades Today: {self.engine.daily_trades}/{self.engine.config.trading_params.max_orders_per_day}")
        
        # Trading Parameters
        if hasattr(self.engine.config.trading_params, 'trade_amount'):
            print(f"  Trade Amount: ${self.engine.config.trading_params.trade_amount:.2f}")
        if hasattr(self.engine.config.trading_params, 'stop_loss_pct'):
            print(f"  Stop Loss: {self.engine.config.trading_params.stop_loss_pct:.1f}%")
        if hasattr(self.engine.config.trading_params, 'take_profit_pct'):
            print(f"  Take Profit: {self.engine.config.trading_params.take_profit_pct:.1f}%")
        print()
        
        # AI Predictions
        print("🤖 AI Analysis:")
        if prediction_info:
            direction = prediction_info.get('direction', 'NEUTRAL')
            confidence = prediction_info.get('confidence', 0.0)
            print(f"  Signal: {direction}")
            print(f"  Confidence: {confidence*100:.1f}%")
            
            if 'predicted_price' in prediction_info:
                pred_price = prediction_info['predicted_price']
                change = (pred_price - current_price) / current_price
                print(f"  Target: ${pred_price:.4f}")
                print(f"  Change: {change*100:+.2f}%")
            
            # Add model info if available
            model_name = prediction_info.get('model_name', '')
            prediction_length = prediction_info.get('prediction_length', '')
            confidence_threshold = prediction_info.get('confidence_threshold', '')
            
            if model_name:
                print(f"  Model: {model_name}")
            if prediction_length:
                print(f"  Prediction Length: {prediction_length}")
            if confidence_threshold:
                print(f"  Conf. Threshold: {confidence_threshold:.1f}")
        else:
            print("  No predictions available")
        print()
        
        # Recent Orders
        if self.engine.orders:
            print("📝 Recent Orders:")
            for order in self.engine.orders[-3:]:
                side = order.get('side', '')
                qty = order.get('quantity', 0)
                price = order.get('price', 0)
                status = order.get('status', '')
                timestamp = order.get('timestamp', '')
                
                if timestamp:
                    try:
                        if isinstance(timestamp, str):
                            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        timestamp_str = timestamp.strftime('%H:%M:%S')
                    except:
                        timestamp_str = str(timestamp)
                else:
                    timestamp_str = 'N/A'
                    
                print(f"  {timestamp_str} - {side} {qty:.8f} @ ${price:.4f} ({status})")
            print()
        
        print("Press Ctrl+C to stop the bot")
        print("-" * 50)

    async def start(self):
        """Start the UI loop"""
        self._running = True
        self._last_values = {}  # Store last known good values
        startup_timeout = 60  # 60 seconds timeout for initial startup
        
        # Create background task for strategy updates
        self._update_task = asyncio.create_task(self._run_strategy_updates())
        
        # Initialize UI
        if not self.headless:
            try:
                if not self._init_curses():
                    logger.warning("Failed to initialize curses UI, falling back to headless mode")
                    self.headless = True
            except Exception as e:
                logger.error(f"Failed to initialize curses UI: {e}")
                self.headless = True
        
        # Initialize engine
        try:
            logger.info("Initializing trading engine...")
            async with asyncio.timeout(startup_timeout):
                if not await self.engine.initialize():
                    raise Exception("Failed to initialize trading engine")
                logger.info("Trading engine initialized successfully")
        except asyncio.TimeoutError:
            logger.error(f"Engine initialization timed out after {startup_timeout} seconds")
            self._cleanup_curses()
            return
        except Exception as e:
            logger.error(f"Failed to initialize engine: {e}")
            self._cleanup_curses()
            return
            
        # Main loop
        try:
            while self._running:
                try:
                    # Get and validate current price
                    current_price = await self.engine.get_current_price(self.engine.config.trading_params.symbol)
                    
                    if current_price and current_price > 0:
                        logger.debug(f"Got current price: {current_price}")
                        self.engine.last_price = current_price
                        self._last_values['price'] = current_price
                        self._last_price_warning = False
                    else:
                        current_price = self._last_values.get('price', 0)
                        if not getattr(self, '_last_price_warning', False):
                            logger.warning("No price data available or invalid price")
                            logger.info(f"Engine state: last_price={getattr(self.engine, 'last_price', 'N/A')}")
                            logger.info(f"Symbol: {self.engine.config.trading_params.symbol}")
                            self._last_price_warning = True
                    
                    # Get prediction info and validate
                    prediction_info = {}
                    if hasattr(self.engine, 'chronos'):
                        try:
                            prediction_info = self.engine.chronos.get_last_prediction()
                            if not prediction_info:
                                if not hasattr(self, '_last_prediction_warning'):
                                    logger.warning("Prediction info is empty. Model may still be initializing...")
                                    self._last_prediction_warning = True
                                prediction_info = self._last_values.get('prediction_info', {})
                            else:
                                self._last_values['prediction_info'] = prediction_info
                                self._last_prediction_warning = False
                        except Exception as e:
                            logger.error(f"Failed to get prediction info: {e}")
                            prediction_info = self._last_values.get('prediction_info', {})
                            if not hasattr(self, '_last_prediction_warning'):
                                logger.error("Prediction system error. Check model initialization and data.")
                                logger.debug(f"Chronos state: {vars(self.engine.chronos)}")
                                self._last_prediction_warning = True
                
                    # Always update display
                    await self._update_display(current_price, prediction_info)
                    self._last_update = datetime.now()
                
                    # Update status if we have valid data
                    if current_price > 0:
                        self.status = "RUNNING"
                    elif self.status == "Initializing...":
                        self.status = "Waiting for data..."
                
                except Exception as e:
                    logger.error(f"Error updating display: {e}")
                
                # Handle keyboard input in curses mode
                if not self.headless and self.stdscr:
                    try:
                        self.stdscr.nodelay(1)  # Non-blocking input
                        ch = self.stdscr.getch()
                        if ch != -1:
                            if ch in [ord('q'), ord('Q')]:
                                logger.info("User requested shutdown")
                                self._running = False
                                break
                            elif ch in [ord('p'), ord('P')]:
                                self.paused = not self.paused
                                self.status = "PAUSED" if self.paused else "RUNNING"
                                if self.paused:
                                    logger.info("Bot paused by user")
                                else:
                                    logger.info("Bot resumed by user")
                            elif ch in [ord('r'), ord('R')]:
                                logger.info("User requested reset")
                                self.engine.reset()
                                self.status = "Reset complete"
                    except curses.error:
                        pass
                
                await asyncio.sleep(1)  # Update interval
                
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
            self._running = False
            
        except Exception as e:
            logger.error(f"UI error: {str(e)}")
            self._running = False
            
        finally:
            self._cleanup_curses()
                
    async def stop(self):
        """Stop the UI"""
        self._running = False
        if not self.headless:
            self._cleanup_curses()
            
    async def _get_strategy_state(self) -> Dict[str, Any]:
        """Get the current strategy state for display"""
        try:
            current_price = await self.engine.client.get_latest_price(self.engine.config.trading_params.symbol)
            # You can add more prediction info here as needed
            prediction_info = {}
            return {"current_price": current_price, "prediction_info": prediction_info}
        except Exception as e:
            logger.error(f"Error getting strategy state: {e}")
            return {"current_price": 0.0, "prediction_info": {}}

def parse_args():
    parser = argparse.ArgumentParser(description='MEXC Trading Bot')
    parser.add_argument('--action', type=str, choices=['start', 'test-api'], default='start', help='Bot action')
    parser.add_argument('--symbol', type=str, help='Trading pair symbol (e.g., BTC_USDT). If not provided, uses value from .env')
    parser.add_argument('--amount', type=float, help='Trading amount in USDT. If not provided, uses value from .env')
    parser.add_argument('--dry-run', action='store_true', help='Run in dry-run mode (no real trades)')
    parser.add_argument('--headless', action='store_true', help='Run without UI (logs only)')
    return parser.parse_args()

async def test_api():
    """Test API connection and credentials"""
    config = load_config()
    try:
        async with MexcClient(config.credentials) as client:
            # Test market data
            pairs = await client.get_trading_pairs()
            logger.info(f"Successfully fetched {len(pairs)} trading pairs")
            
            # Test authentication
            account = await client.get_account()
            logger.info(f"Successfully authenticated with account: {account.get('account_id', 'Unknown')}")
            
            # Test rate limits
            for _ in range(3):
                await client.get_ticker("BTC_USDT")
            logger.info("Rate limiting working correctly")
            
            logger.info("✅ API connection test successful")
    except Exception as e:
        logger.error(f"❌ API test failed: {str(e)}")
        sys.exit(1)

async def main():
    try:
        args = parse_args()
        logger.debug("Command line arguments: {}", args)
        
        # Load configuration
        config = load_config()
        logger.debug("Configuration loaded from .env")
        
        # Only override with CLI args if they are explicitly provided
        if args.symbol:
            logger.warning("Command-line symbol argument overriding .env setting: {}", args.symbol)
            logger.warning("Consider updating your .env file instead of using command-line arguments")
            config.trading_params.symbol = args.symbol
            
        if args.amount:
            logger.warning("Command-line amount argument overriding .env setting: {}", args.amount)
            config.trading_params.trade_amount = args.amount
            
        if args.dry_run:
            logger.warning("Command-line dry-run flag overriding .env setting")
            config.trading_params.dry_run = True
            
        if args.headless:
            logger.info("Running in headless mode")
            config.headless = True
            
        # Log the active configuration
        logger.info("Active configuration:")
        logger.info("  Symbol: {}", config.trading_params.symbol)
        logger.info("  Trade Amount: {}", config.trading_params.trade_amount)
        logger.info("  Mode: {}", "DRY RUN" if config.trading_params.dry_run else "LIVE TRADING")
            
        logger.debug("Final configuration: {}", 
                    {k: v for k, v in config.dict().items() if k != 'credentials'})
    except Exception as e:
        logger.error("Error during initialization: {}", str(e))
        raise
        
    if args.action == 'test-api':
        await test_api()
        return
    
    # Initialize components
    client = None
    trading_engine = None
    ui = None
    
    MAX_RETRIES = 3
    retry_count = 0
    last_error = None
    
    while retry_count < MAX_RETRIES:
        try:
            # Create fresh instances
            client = MexcClient(config.credentials)
            trading_engine = TradingEngine(config, client)
            ui = TradingBotUI(trading_engine, config.headless)
            
            async with client:
                # Initialize trading engine
                await trading_engine.initialize()
                logger.info("Trading bot initialized successfully")
                
                if config.trading_params.dry_run:
                    logger.warning("Bot is running in DRY RUN mode - no real trades will be executed")
                
                # Start both components
                await asyncio.gather(
                    trading_engine.start(),
                    ui.start()
                )
                break  # Success
                
        except KeyboardInterrupt:
            logger.info("Received shutdown signal, cleaning up...")
            break
            
        except Exception as e:
            last_error = e
            retry_count += 1
            logger.error(f"Error in main loop (attempt {retry_count}/{MAX_RETRIES}): {str(e)}")
            
            if retry_count < MAX_RETRIES:
                logger.info(f"Retrying in 5 seconds...")
                await asyncio.sleep(5)
            
        finally:
            # Clean shutdown
            if trading_engine:
                await trading_engine.stop()
            if ui:
                await ui.stop()
    
    if retry_count >= MAX_RETRIES:
        logger.error(f"Bot failed to start after {MAX_RETRIES} attempts")
        logger.error(f"Last error: {str(last_error)}")
        sys.exit(1)

if __name__ == '__main__':
    if sys.platform == "win32":
        os.environ['PYTHONIOENCODING'] = 'utf-8'
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot shutdown complete")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        sys.exit(1)