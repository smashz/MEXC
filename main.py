#!/usr/bin/env python3
from typing import Optional, Dict, Any, List
import argparse
import curses
import os
import sys
import time
import asyncio
from datetime import datetime
from typing import Dict, Any
from config import load_config, BotConfig
from mexc_client import MexcClient
from trading_engine import TradingEngine

# Configure logger
from loguru import logger

logger.configure(
    handlers=[
        {
            "sink": sys.stdout,
            "format": "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        }
    ]
)

# Constants for UI
HEADER = "MEXC Trading Bot"
MIN_TERMINAL_WIDTH = 80
MIN_TERMINAL_HEIGHT = 24

class TradingBotUI:
    def __init__(self, engine: TradingEngine, headless: bool = False):
        """Initialize the UI"""
        try:
            self.engine = engine
            self.headless = headless
            self.stdscr = None
            self._running = False
            self._curses_failed = False
            self._retry_count = 0
            self.MAX_RETRIES = 3
            self._last_update = datetime.now()
            self.session_start_time = datetime.now()
            self.status = "Initializing..."
            self.paused = False
            
            # Update intervals and checks
            self._update_interval = getattr(engine.config.trading_params, 'ui_update_interval', 1.0)
            self._last_check = {}  # Track last check times for different components
            self._last_full_refresh = 0.0  # Track last full refresh time
            self._last_env_mtime = os.path.getmtime('.env')  # Track .env file modification time
            self._env_check_interval = 1.0  # Check for .env changes every second
            
            # State tracking
            self._last_price = 0.0  # Track last price for minimal updates
            self._last_status = ""  # Track last status for change detection
            self._last_values = {}  # Track last known good values
            self._full_refresh_needed = True  # Force initial full refresh
            
            # Session tracking
            self.session_trades = []
            self.session_wins = 0
            self.session_losses = 0
            self.session_start_balance = 0.0
            self.session_current_balance = 0.0
            self.session_peak_balance = 0.0
            self.session_pnl = 0.0

            # Session statistics
            self.session_start_balance = 0.0
            self.session_current_balance = 0.0
            self.session_peak_balance = 0.0
            self.session_trades = []
            self.session_wins = 0
            self.session_losses = 0
            self.session_pnl = 0.0
            self.last_trade_time = None
            self._full_refresh_needed = True  # Force full refresh initially
            self._last_status = ""  # Track last status for minimal updates
            self._full_refresh_needed = True  # Flag for full screen refresh
            self.last_prediction = None  # Track last prediction for change detection
            self.session_start_balance = 0.0
            self.session_current_balance = 0.0
            self.session_start_time = datetime.now()
        except Exception as e:
            logger.error(f"Error initializing UI: {e}")
            raise

    def _format_minimal_status(self, current_price: float) -> str:
        """Format the minimal status line for headless mode"""
        try:
            status = "PAUSED" if self.paused else self.status
            mode = "DRY RUN" if self.engine.config.trading_params.dry_run else "LIVE"
            return f"\rPrice: ${current_price:.4f} | Mode: {mode} | Status: {status}"
        except Exception as e:
            logger.error(f"Error formatting minimal status: {e}")
            return f"\rPrice: ${current_price:.4f}"

    def _format_prediction_info(self, prediction_info: Dict[str, Any]) -> str:
        """Format prediction info for display"""
        try:
            if not prediction_info:
                return ""
            direction = prediction_info.get('direction', 'NEUTRAL')
            confidence = prediction_info.get('confidence', 0.0)
            trend = "^" if direction == "BUY" else "v" if direction == "SELL" else "-"
            return f"\nAI Prediction: [{trend}]\nSignal: {direction}\nConfidence: {confidence*100:.1f}%"
        except Exception as e:
            logger.error(f"Error formatting prediction: {e}")
            return ""

    def _format_balance_info(self) -> str:
        """Format balance info for display"""
        try:
            if not hasattr(self, 'session_start_balance') or not hasattr(self, 'session_current_balance'):
                return "Balance: Initializing..."
            
            if self.session_start_balance is None or self.session_start_balance <= 0:
                return "Balance: Initializing..."
                
            session_pnl = self.session_current_balance - self.session_start_balance
            pnl_percent = (session_pnl / self.session_start_balance) * 100
            return f"Balance: ${self.session_current_balance:.2f}\nP/L: ${session_pnl:+.2f} ({pnl_percent:+.2f}%)"
        except Exception as e:
            logger.error(f"Error formatting balance: {e}")
            return "Balance: Error"

    async def _print_headless_status(self, current_price: float, prediction_info: Dict[str, Any]):
        """Display status in headless mode"""
        try:
            # For minimal updates, just show the status line
            if not self._full_refresh_needed:
                print(self._format_minimal_status(current_price), end="", flush=True)
                return

            # Full refresh starts with clearing the screen
            print("\033[2J\033[H", end="", flush=True)
            
            # Calculate session duration
            session_duration = datetime.now() - self.session_start_time
            hours = session_duration.seconds // 3600
            minutes = (session_duration.seconds % 3600) // 60
            seconds = session_duration.seconds % 60

            # Build the full display with colors
            mode = "DRY RUN" if self.engine.config.trading_params.dry_run else "LIVE TRADING"
            mode_color = "\033[33m" if self.engine.config.trading_params.dry_run else "\033[32m"  # Yellow for dry run, green for live
            
            lines = [
                "\033[1m\033[36mMEXC AI Trading Bot (Headless Mode)\033[0m",  # Cyan bold header
                "=" * 80,
                "",
                f"Status: {mode_color}{'|| ' if self.paused else '> '}{self.paused and 'PAUSED' or self.status}\033[0m",
                f"Last Update: {self._last_update.strftime('%H:%M:%S')}",
                f"Session Duration: {hours:02d}:{minutes:02d}:{seconds:02d}",
                "",
                f"Mode: {mode_color}{mode}\033[0m",
                f"Symbol: \033[1m{self.engine.config.trading_params.symbol}\033[0m",
                f"Current Price: \033[1m${current_price:.4f}\033[0m",
                "",
                "\033[1mAccount Information:\033[0m",
                "-" * 40,
                self._format_balance_info(),
                "",
                "\033[1mSession Statistics:\033[0m",
                "-" * 40,
                f"Total Trades: \033[1m{len(self.session_trades)}\033[0m",
                f"Wins/Losses: \033[32m{self.session_wins}\033[0m/\033[31m{self.session_losses}\033[0m",
                f"Win Rate: \033[1m{(self.session_wins / len(self.session_trades) * 100):.1f}%\033[0m" if self.session_trades else "Win Rate: \033[1mN/A\033[0m",
                f"Peak Balance: \033[1m${self.session_peak_balance:.2f}\033[0m",
                f"Session PnL: {'\\033[32m' if self.session_pnl >= 0 else '\\033[31m'}${self.session_pnl:+.2f}\033[0m",
            ]
            
            # Add prediction info if available
            if prediction_info:
                lines.extend([
                    "",
                    "Market Analysis:",
                    "-" * 40,
                    self._format_prediction_info(prediction_info)
                ])
            
            # Add recent trades if available
            if self.session_trades:
                lines.extend([
                    "",
                    "Recent Trades:",
                    "-" * 40,
                ])
                # Show last 5 trades
                for trade in self.session_trades[-5:]:
                    trade_time = trade['time'].strftime('%H:%M:%S')
                    trade_type = trade['type']
                    trade_price = trade['price']
                    trade_pnl = trade['pnl']
                    trade_result = '✅' if trade_pnl > 0 else '❌' if trade_pnl < 0 else '➖'
                    lines.append(f"{trade_result} {trade_time} {trade_type} @ ${trade_price:.4f} (${trade_pnl:+.2f})")
            
            # Add footer with commands
            lines.extend([
                "",
                "Commands: [p]ause | [r]esume | [q]uit",
                "=" * 80
            ])
            
            # Print all lines
            print("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Error in headless display: {e}")
            # Fallback to minimal status line
            try:
                print(self._format_minimal_status(current_price), end="", flush=True)
            except Exception as e2:
                logger.error(f"Failed to display fallback status: {e2}")

    def _update_session_stats(self, trade_info: Dict[str, Any]) -> None:
        """Update session statistics with new trade information"""
        try:
            # Add trade to history
            trade_info['time'] = datetime.now()
            self.session_trades.append(trade_info)
            
            # Update win/loss counts
            if trade_info['pnl'] > 0:
                self.session_wins += 1
            elif trade_info['pnl'] < 0:
                self.session_losses += 1
            
            # Update total PnL
            self.session_pnl += trade_info['pnl']
            
            # Update balance
            self.session_current_balance = self.session_start_balance + self.session_pnl
            self.session_peak_balance = max(self.session_peak_balance, self.session_current_balance)
            
            # Set last trade time
            self.last_trade_time = datetime.now()
            
            # Force full refresh of display
            self._full_refresh_needed = True
            
        except Exception as e:
            logger.error(f"Error updating session stats: {e}")

    async def _update_account_info(self) -> None:
        """Update account balance information"""
        try:
            logger.debug("Fetching account information...")
            
            if self.engine.config.trading_params.dry_run:
                # Use simulated balance for dry run
                logger.debug("Dry run mode - using simulated balance")
                self.session_start_balance = 1000.0  # Start with 1000 USDT in dry run
                self.session_peak_balance = self.session_start_balance
                self.session_current_balance = self.session_start_balance
                self.status = "Running (Dry Run)"
                return

            # Get real account info for live trading
            account_info = await self.engine.client.get_account()
            logger.debug(f"Raw account info: {account_info}")
            
            if account_info and 'balances' in account_info:
                base_asset = self.engine.config.trading_params.symbol.split('_')[0]
                quote_asset = self.engine.config.trading_params.symbol.split('_')[1]
                logger.debug(f"Processing balances for {base_asset} and {quote_asset}")
                
                # Calculate total balance in quote currency
                total_balance = 0.0
                for balance in account_info['balances']:
                    if balance['asset'] in [base_asset, quote_asset]:
                        if balance['asset'] == quote_asset:
                            amount = float(balance['free']) + float(balance['locked'])
                            logger.debug(f"{quote_asset} balance: {amount}")
                            total_balance += amount
                        else:
                            # Convert base asset to quote using current price
                            current_price = await self.engine.client.get_ticker_price(self.engine.config.trading_params.symbol)
                            amount = float(balance['free']) + float(balance['locked'])
                            base_value = amount * float(current_price['price'])
                            logger.debug(f"{base_asset} balance: {amount} (Value: {base_value} {quote_asset})")
                            total_balance += base_value
                
                # Update session balance information
                if self.session_start_balance <= 0:
                    self.session_start_balance = total_balance
                    self.session_peak_balance = total_balance
                    logger.info(f"Initial balance: {total_balance} {quote_asset}")
                self.session_current_balance = total_balance
                self.session_peak_balance = max(self.session_peak_balance, total_balance)
                self.status = "Running"
                
        except Exception as e:
            logger.error(f"Error updating account info: {e}")

    async def _get_prediction_info(self) -> Dict[str, Any]:
        """Get the latest prediction information"""
        prediction_info = {}
        try:
            if hasattr(self.engine, 'chronos') and self.engine.chronos:
                prediction_info = self.engine.chronos.get_last_prediction() or {
                    'direction': 'NEUTRAL',
                    'confidence': 0.0,
                    'prediction': None
                }
                # Detect changes
                if prediction_info != self.last_prediction:
                    self._full_refresh_needed = True
                    self.last_prediction = prediction_info
        except Exception as e:
            logger.error(f"Error getting prediction info: {e}")
        return prediction_info

    async def _check_config_changes(self) -> bool:
        """Check if the config needs to be reloaded"""
        try:
            current_mtime = os.path.getmtime('.env')
            if current_mtime != self._last_env_mtime:
                logger.info("Config file changed, reloading...")
                self._last_env_mtime = current_mtime
                self._full_refresh_needed = True
                return True
            return False
        except Exception as e:
            logger.error(f"Error checking config changes: {e}")
            return False

    async def start(self):
        """Start the UI loop"""
        self._running = True
        startup_timeout = 60  # 60 seconds timeout for initial startup
        self.session_start_time = datetime.now()
        
        try:
            logger.info("Starting bot initialization...")
            
            # First initialize trading engine
            async with asyncio.timeout(startup_timeout):
                logger.info("Initializing trading engine...")
                if not await self.engine.initialize():
                    raise Exception("Failed to initialize trading engine")
                logger.info("Trading engine initialized successfully")
            
            # Then initialize account info
            logger.info("Fetching initial account information...")
            await self._update_account_info()
            logger.info("Account information retrieved successfully")
            
            # Initialize strategy
            logger.info("Initializing trading strategy...")
            prediction_info = await self._get_prediction_info()
            if prediction_info:
                logger.info("Strategy initialization complete")
            else:
                logger.warning("Strategy initialization returned no prediction info")
                
            # Initialize account info
            await self._update_account_info()
            
            while self._running:
                try:
                    # Update account info periodically (every minute)
                    current_time = time.time()
                    if current_time - self._last_check.get('account_info', 0) >= 60:
                        await self._update_account_info()
                        self._last_check['account_info'] = current_time
                        
                    # Get and validate current price
                    current_price = await self.engine.client.get_ticker_price(self.engine.config.trading_params.symbol)
                    current_price = float(current_price['price'])
                    
                    # Get prediction info
                    prediction_info = await self._get_prediction_info()
                    
                    # Update display
                    if self.headless:
                        await self._print_headless_status(current_price, prediction_info)
                    else:
                        await self._update_display(current_price, prediction_info)
                    
                    # Check for config changes
                    config_changed = await self._check_config_changes()
                    if config_changed:
                        await self.engine.reload_config()
                    
                    # Sleep for update interval
                    await asyncio.sleep(self._update_interval)
                    
                except asyncio.TimeoutError:
                    logger.error("Operation timed out")
                except Exception as e:
                    logger.error(f"Error in UI loop: {str(e)}")
                    await asyncio.sleep(1)  # Sleep briefly on error
                try:
                    # Update account info periodically
                    current_time = time.time()
                    if current_time - self._last_check.get('account_info', 0) >= 60:
                        logger.debug("Updating account information...")
                        await self._update_account_info()
                        self._last_check['account_info'] = current_time
                        
                    # Get and validate current price
                    price_response = await self.engine.client.get_ticker_price(self.engine.config.trading_params.symbol)
                    current_price = float(price_response['price'])
                    
                    # Update state and check if full refresh is needed
                    if current_price > 0:
                        logger.debug(f"Current price: {current_price:.4f}")
                        self.engine.last_price = current_price
                        self._last_values['price'] = current_price
                        
                        # Determine if we need a full refresh
                        price_changed = abs(current_price - self._last_price) > 0.0001
                        status_changed = self.status != self._last_status
                        time_for_refresh = time.time() - self._last_full_refresh > 5.0  # Full refresh every 5 seconds
                        self._full_refresh_needed = price_changed or status_changed or time_for_refresh
                    
                    # Get prediction info
                    prediction_info = await self._get_prediction_info()
                    
                    # Update the display
                    await self._print_headless_status(current_price, prediction_info)
                    
                    # Update tracking variables after successful update
                    self._last_price = current_price
                    self._last_status = self.status
                    self._last_update = datetime.now()
                    
                    # Clear full refresh flag after successful update
                    self._full_refresh_needed = False
                    
                    # Sleep for the update interval
                    await asyncio.sleep(self._update_interval)
                    
                except Exception as e:
                    logger.error(f"Error in main loop: {e}")
                    await asyncio.sleep(1)  # Sleep on error to avoid tight loop
                    
        except asyncio.TimeoutError:
            logger.error(f"Engine initialization timed out after {startup_timeout} seconds")
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            logger.error(f"Fatal error in UI: {e}")
        finally:
            self._running = False
            await self.stop()

    async def stop(self):
        """Stop the UI"""
        self._running = False
        if hasattr(self.engine, 'stop'):
            try:
                await self.engine.stop()
            except Exception as e:
                logger.error(f"Error stopping engine: {e}")

def parse_args():
    parser = argparse.ArgumentParser(description='MEXC Trading Bot')
    parser.add_argument('--action', type=str, choices=['start', 'test-api'], default='start', help='Bot action')
    parser.add_argument('--symbol', type=str, help='Trading pair symbol (e.g., BTC_USDT). If not provided, uses value from .env')
    parser.add_argument('--amount', type=float, help='Trading amount in USDT. If not provided, uses value from .env')
    parser.add_argument('--dry-run', action='store_true', help='Run in dry-run mode (no real trades)')
    parser.add_argument('--headless', action='store_true', help='Run without UI (logs only)')
    return parser.parse_args()

async def main():
    try:
        args = parse_args()
        logger.debug("Command line arguments: {}", args)
        
        # Load configuration
        config = load_config()
        logger.debug("Configuration loaded from .env")
        
        # Override config with CLI args if provided
        if args.symbol:
            logger.warning("Command-line symbol argument overriding .env setting: {}", args.symbol)
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
            
        # Initialize components with better error handling
        try:
            client = MexcClient(config.credentials)
            trading_engine = TradingEngine(config, client)
            ui = TradingBotUI(trading_engine, config.headless)
            
            # Start the UI and trading engine within the API client context
            async with client:
                logger.debug("API client initialized successfully")
                
                try:
                    await ui.start()
                except KeyboardInterrupt:
                    logger.info("Received shutdown signal")
                except ValueError as e:
                    logger.error(f"Configuration error: {e}")
                    raise
                except Exception as e:
                    logger.error(f"Error running UI: {e}")
                    raise
                finally:
                    await ui.stop()
                    
        except ValueError as e:
            logger.error(f"Setup error: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
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
