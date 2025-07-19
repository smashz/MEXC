#!/usr/bin/env python3

import os
import sys
import pytz
import asyncio
import argparse
from typing import Optional
from datetime import datetime, time as time_obj
from loguru import logger
from config import load_config, BotConfig
from mexc_client import MexcClient
from trading_engine import TradingEngine

# Fix Windows Unicode encoding issues
if sys.platform == "win32":
    # Set UTF-8 encoding for stdout and stderr on Windows
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')
    
    # Set environment variable for UTF-8 encoding
    os.environ['PYTHONIOENCODING'] = 'utf-8'

async def wait_until_time(target_time_str: str, timezone_str: str = "America/New_York"):
    """Wait until a specific time in the given timezone"""
    try:
        # Parse the target time (format: "HH:MM" or "HH:MM:SS")
        if len(target_time_str.split(':')) == 2:
            target_time_str += ":00"  # Add seconds if not provided
        
        target_time = datetime.strptime(target_time_str, "%H:%M:%S").time()
        
        # Get timezone
        tz = pytz.timezone(timezone_str)
        
        # Get current time in the target timezone
        now = datetime.now(tz)
        current_date = now.date()
        
        # Create target datetime for today
        target_datetime = datetime.combine(current_date, target_time)
        target_datetime = tz.localize(target_datetime)
        
        # If target time has already passed today, schedule for tomorrow
        if target_datetime <= now:
            target_datetime = target_datetime.replace(day=current_date.day + 1)
            target_datetime = tz.localize(target_datetime.replace(tzinfo=None))
        
        # Calculate wait time
        wait_seconds = (target_datetime - now).total_seconds()
        
        print(f"⏰ Scheduling order for {target_datetime.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"🕐 Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"⏳ Waiting {wait_seconds:.0f} seconds ({wait_seconds/60:.1f} minutes)...")
        
        # Wait until target time
        if wait_seconds > 0:
            # Show countdown for the last 10 seconds
            if wait_seconds <= 10:
                for i in range(int(wait_seconds), 0, -1):
                    print(f"⏰ Executing in {i} seconds...")
                    await asyncio.sleep(1)
            else:
                # For longer waits, show periodic updates
                while wait_seconds > 10:
                    await asyncio.sleep(min(60, wait_seconds - 10))  # Check every minute or until 10 seconds left
                    current_time = datetime.now(tz)
                    remaining = (target_datetime - current_time).total_seconds()
                    if remaining <= 10:
                        break
                    print(f"⏳ {remaining/60:.1f} minutes remaining until execution...")
                    wait_seconds = remaining
                
                # Final countdown
                final_wait = (target_datetime - datetime.now(tz)).total_seconds()
                if final_wait > 0:
                    for i in range(int(final_wait), 0, -1):
                        print(f"⏰ Executing in {i} seconds...")
                        await asyncio.sleep(1)
        
        print(f"▶️ Executing order at {datetime.now(tz).strftime('%H:%M:%S %Z')}!")
        
    except ValueError as e:
        raise ValueError(f"Invalid time format. Use HH:MM or HH:MM:SS format. Error: {e}")
    except Exception as e:
        raise Exception(f"Error scheduling order: {e}")

class TradingBot:
    """Main trading bot orchestrator"""
    
    def __init__(self, config: BotConfig):
        self.config = config
        self.client: Optional[MexcClient] = None
        self.engine: Optional[TradingEngine] = None
        self.running = False
        
    async def initialize(self):
        """Initialize bot components"""
        try:
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
            
            # Validate configuration
            if not self.config.credentials.api_key or not self.config.credentials.secret_key:
                raise ValueError("MEXC API credentials not configured. Please set MEXC_API_KEY and MEXC_SECRET_KEY environment variables.")
            
            # Initialize MEXC client
            self.client = MexcClient(
                credentials=self.config.credentials,
                rate_limit_rps=self.config.rate_limit_requests_per_second
            )
            
            # Initialize trading engine
            self.engine = TradingEngine(self.config, self.client)
            
            logger.info("Trading bot initialized successfully")
            
            if self.config.dry_run:
                logger.warning("Bot is running in DRY RUN mode - no real trades will be executed")
            
        except Exception as e:
            logger.error(f"Failed to initialize bot: {str(e)}")
            raise
    
    async def start(self):
        """Start the trading bot"""
        if not self.client or not self.engine:
            await self.initialize()
        
        self.running = True
        logger.info("Starting trading bot...")
        
        async with self.client:
            # Start position monitoring task
            monitor_task = asyncio.create_task(self.engine.monitor_positions())
            
            try:
                # Main bot loop
                while self.running:
                    await asyncio.sleep(1)
                    
            except KeyboardInterrupt:
                logger.info("Received shutdown signal")
                self.running = False
            finally:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
                
                logger.info("Trading bot stopped")
    
    async def stop(self):
        """Stop the trading bot"""
        self.running = False
        logger.info("Stopping trading bot...")
    
    async def place_buy_order(self, price: float, quantity: Optional[float] = None):
        """Place a buy order"""
        if not self.engine:
            await self.initialize()
        
        async with self.client:
            # Validate symbol first
            symbol = self.config.trading_params.symbol
            print(f"🔍 Validating trading symbol: {symbol}")
            
            is_valid = await self.client.validate_symbol(symbol)
            if not is_valid:
                print(f"❌ Symbol '{symbol}' is not valid or not supported for trading")
                print("💡 Suggestions:")
                print("  • Check the symbol format (should be like BTCUSDT, ETHUSDT)")
                print("  • Use --symbol flag to override (e.g., --symbol BTCUSDT)")
                print("  • Run 'python main.py --action symbols --search BTC' to find valid symbols")
                return None
            
            print(f"✅ Symbol '{symbol}' is valid for trading")
            
            result = await self.engine.place_limit_buy_order(price, quantity)
            if result:
                logger.info(f"Buy order result: {result}")
                return result
            else:
                logger.warning("Buy order was not placed")
                return None
    
    async def place_sell_order(self, price: float, quantity: Optional[float] = None):
        """Place a sell order"""
        if not self.engine:
            await self.initialize()
        
        async with self.client:
            # Validate symbol first
            symbol = self.config.trading_params.symbol
            print(f"🔍 Validating trading symbol: {symbol}")
            
            is_valid = await self.client.validate_symbol(symbol)
            if not is_valid:
                print(f"❌ Symbol '{symbol}' is not valid or not supported for API trading")
                print("💡 Suggestions:")
                print("  • Check the symbol format (should be like BTCUSDT, ETHUSDT)")
                print("  • Use --symbol flag to override (e.g., --symbol BTCUSDT)")
                print("  • Run 'python main.py --action symbols --search BTC' to find valid symbols")
                return None
            
            print(f"✅ Symbol '{symbol}' is valid for trading")
            
            result = await self.engine.place_limit_sell_order(price, quantity)
            if result:
                logger.info(f"Sell order result: {result}")
                return result
            else:
                logger.warning("Sell order was not placed")
                return None
    
    async def get_status(self):
        """Get bot and account status"""
        if not self.engine:
            await self.initialize()
        
        async with self.client:
            summary = await self.engine.get_account_summary()
            
            # Add bot-specific status
            summary.update({
                "bot_running": self.running,
                "dry_run_mode": self.config.dry_run,
                "trading_symbol": self.config.trading_params.symbol,
                "stop_loss_percentage": self.config.trading_params.stop_loss_percentage,
                "trading_windows": [
                    {
                        "start": window.start_time,
                        "end": window.end_time,
                        "timezone": window.timezone
                    }
                    for window in self.config.trading_windows
                ]
            })
            
            return summary
    
    async def search_symbols(self, search_term: Optional[str] = None):
        """Search for available trading symbols"""
        if not self.client:
            await self.initialize()
        
        async with self.client:
            # Test connectivity first
            print("🔗 Testing API connectivity...")
            connectivity = await self.client.test_connectivity()
            if not connectivity:
                print("❌ API connectivity test failed. Please check your internet connection and API credentials.")
                return
            
            print("✅ API connectivity successful")
            
            # Test server time
            try:
                server_time = await self.client.get_server_time()
                print(f"🕒 Server time: {server_time.get('serverTime', 'Unknown')}")
            except Exception as e:
                print(f"⚠️  Could not get server time: {e}")
            
            if search_term:
                print(f"\n🔍 Searching for symbols containing '{search_term}'...")
                symbols = await self.client.search_symbols(search_term)
                print(f"🔍 Symbols containing '{search_term}':")
                if symbols:
                    for i, symbol in enumerate(symbols[:20], 1):  # Show first 20 results
                        print(f"  {i:2d}. {symbol}")
                    if len(symbols) > 20:
                        print(f"  ... and {len(symbols) - 20} more")
                else:
                    print(f"  No symbols found containing '{search_term}'")
                    
                    # If no results, suggest alternatives
                    print("\n💡 Troubleshooting suggestions:")
                    print("  • Try a different search term (e.g., BTC, ETH, USDT)")
                    print("  • Check if the symbol exists on MEXC exchange")
                    print("  • Verify your API credentials and permissions")
            else:
                print("\n📊 Getting all available symbols...")
                # Get all symbols first
                all_symbols = await self.client.get_all_symbols()
                
                if not all_symbols:
                    print("❌ No symbols found. This could indicate:")
                    print("  • API authentication issues")
                    print("  • Network connectivity problems")
                    print("  • Regional restrictions")
                    print("  • MEXC API maintenance")
                    
                    # Try to get exchange info directly for debugging
                    try:
                        print("\n🔧 Attempting direct exchange info call...")
                        exchange_info = await self.client.get_exchange_info()
                        symbols_raw = exchange_info.get('symbols', [])
                        print(f"📋 Raw exchange info returned {len(symbols_raw)} symbols")
                        
                        if symbols_raw:
                            print("📝 Sample symbols from raw data:")
                            for i, symbol in enumerate(symbols_raw[:5]):
                                print(f"  {i+1}. {symbol.get('symbol', 'Unknown')} - Status: {symbol.get('status', 'Unknown')}")
                        
                    except Exception as e:
                        print(f"❌ Direct exchange info call failed: {e}")
                    
                    return
                
                # Filter for USDT pairs
                usdt_symbols = [s for s in all_symbols if s.endswith('USDT')]
                
                # Find popular pairs
                popular_pairs = []
                for symbol in usdt_symbols:
                    base = symbol.replace('USDT', '')
                    if base in ["BTC", "ETH", "ADA", "XRP", "DOT", "LTC", "BCH", "LINK", "UNI", "DOGE"]:
                        popular_pairs.append(symbol)
                
                print("\n💰 Popular USDT Trading Pairs:")
                if popular_pairs:
                    for symbol in popular_pairs:
                        print(f"  • {symbol}")
                else:
                    print("  No popular pairs found")
                    
                # Show first 10 USDT pairs if any exist
                if usdt_symbols:
                    print("\n📈 Available USDT pairs:")
                    for symbol in usdt_symbols[:10]:
                        print(f"  • {symbol}")
                    if len(usdt_symbols) > 10:
                        print(f"  ... and {len(usdt_symbols) - 10} more")
                
                print(f"\n📊 Symbol Statistics:")
                print(f"  • Total symbols: {len(all_symbols)}")
                print(f"  • USDT pairs: {len(usdt_symbols)}")
                
                # Show breakdown by quote currency
                quote_breakdown = {}
                for symbol in all_symbols:
                    for quote in ['USDT', 'BTC', 'ETH', 'BNB', 'USDC']:
                        if symbol.endswith(quote):
                            quote_breakdown[quote] = quote_breakdown.get(quote, 0) + 1
                            break
                
                if quote_breakdown:
                    print(f"  • Quote currency breakdown:")
                    for quote, count in sorted(quote_breakdown.items(), key=lambda x: x[1], reverse=True):
                        print(f"    - {quote}: {count} pairs")
                
                print("\n💡 Use --search to find specific symbols (e.g., --search BTC)")

    async def validate_current_symbol(self):
        """Validate the currently configured trading symbol"""
        if not self.client:
            await self.initialize()
        
        async with self.client:
            symbol = self.config.trading_params.symbol
            print(f"🔍 Checking configured symbol: {symbol}")
            
            # Test connectivity first
            connectivity = await self.client.test_connectivity()
            if not connectivity:
                print("❌ API connectivity test failed")
                return False
            
            # Validate symbol
            is_valid = await self.client.validate_symbol(symbol)
            if is_valid:
                print(f"✅ Symbol '{symbol}' is valid for trading")
                
                # Get current price to confirm it's working
                try:
                    ticker = await self.client.get_ticker_price(symbol)
                    price = ticker.get('price', 'Unknown')
                    print(f"💰 Current price: {price}")
                    return True
                except Exception as e:
                    print(f"⚠️  Could not get price for {symbol}: {e}")
                    return False
            else:
                print(f"❌ Symbol '{symbol}' is not valid or not supported for trading")
                
                # Suggest alternatives
                print("\n💡 Suggestions:")
                print("  • Use one of these popular symbols:")
                popular_symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "XRPUSDT", "DOTUSDT"]
                for sym in popular_symbols:
                    print(f"    - {sym}")
                print("  • Update your .env file with TRADING_SYMBOL=BTCUSDT")
                print("  • Or use --symbol flag when running commands")
                
                return False

    async def test_permissions(self):
        """Test API key permissions and account status"""
        if not self.client:
            await self.initialize()
        
        async with self.client:
            print("🔐 Testing API Key Permissions...\n")
            
            permissions = await self.client.test_api_permissions()
            
            print("📋 Permission Test Results:")
            print(f"  🔗 Connectivity: {'✅' if permissions['connectivity'] else '❌'}")
            print(f"  👤 Account Access: {'✅' if permissions['account_access'] else '❌'}")
            print(f"  💱 Trading Enabled: {'✅' if permissions['trading_enabled'] else '❌'}")
            print(f"  🏷️  Account Type: {permissions['account_type']}")
            print(f"  📊 Trading Status: {permissions['trading_status']}")
            
            if permissions['error_details']:
                print("\n❌ Issues Found:")
                for error in permissions['error_details']:
                    print(f"  • {error}")
            
            if not permissions['trading_enabled']:
                print("\n🚨 Trading is disabled! Possible reasons:")
                print("  • API key created without trading permissions")
                print("  • Account not fully verified")
                print("  • Account restricted or suspended")
                print("  • Wrong API endpoint (testnet vs mainnet)")
                print("\n💡 Solutions:")
                print("  • Check MEXC account verification status")
                print("  • Recreate API key with trading permissions enabled")
                print("  • Contact MEXC support if account is restricted")
            
            return permissions

    async def find_tradeable_symbols(self):
        """Find symbols that allow spot trading"""
        if not self.client:
            await self.initialize()
        
        async with self.client:
            print("🔍 Finding symbols that allow spot trading...\n")
            
            # Test connectivity first
            connectivity = await self.client.test_connectivity()
            if not connectivity:
                print("❌ API connectivity test failed")
                return
            
            # Get all tradeable USDT pairs (remove limit to get all)
            tradeable_pairs = await self.client.get_tradeable_usdt_pairs(10000)  # Large number to get all pairs
            
            if not tradeable_pairs:
                print("❌ No tradeable USDT pairs found!")
                print("This could indicate an API or account issue.")
                return
            
            print(f"✅ Found {len(tradeable_pairs)} tradeable USDT pairs:\n")
            
            # Show popular trading pairs first
            popular_bases = ['BTC', 'ETH', 'ADA', 'XRP', 'DOT', 'LTC', 'BCH', 'LINK', 'UNI', 'DOGE']
            popular_found = []
            other_pairs = []
            
            for pair in tradeable_pairs:
                base = pair['baseAsset']
                if base in popular_bases:
                    popular_found.append(pair)
                else:
                    other_pairs.append(pair)
            
            if popular_found:
                print("🌟 Popular Tradeable Pairs:")
                for pair in popular_found:
                    symbol = pair['symbol']
                    base = pair['baseAsset']
                    max_amount = pair['maxQuoteAmount']
                    print(f"  ✅ {symbol} ({base}/USDT) - Max: ${max_amount} USDT")
                
                # Suggest the first popular one
                first_popular = popular_found[0]['symbol']
            
            if other_pairs and len(other_pairs) > 0:
                print(f"\n📈 All Other Available Pairs:")
                for i, pair in enumerate(other_pairs, 1):  # Show all other pairs
                    symbol = pair['symbol']
                    base = pair['baseAsset']
                    max_amount = pair['maxQuoteAmount']
                    print(f"  {i:3d}. {symbol} ({base}/USDT) - Max: ${max_amount} USDT")
            
            print(f"\n📊 Total Summary:")
            print(f"  • Popular pairs found: {len(popular_found)}")
            print(f"  • Other pairs found: {len(other_pairs)}")
            print(f"  • Total tradeable pairs: {len(tradeable_pairs)}")
            
            # Test one of the symbols
            if popular_found:
                test_symbol = popular_found[0]['symbol']
                print(f"\n🧪 Testing symbol validation for {test_symbol}:")
                is_valid = await self.client.validate_symbol(test_symbol)
                if is_valid:
                    print(f"✅ {test_symbol} passes validation - ready for trading!")
                else:
                    print(f"❌ {test_symbol} failed validation")
            
            return tradeable_pairs

    async def debug_tpsl_support(self):
        """Debug TP/SL support for current symbol"""
        if not self.client:
            await self.initialize()
        
        async with self.client:
            symbol = self.config.trading_params.symbol
            print(f"🔍 Debugging TP/SL support for {symbol}...")
            
            # Check TP/SL capabilities
            tpsl_info = await self.client.check_symbol_tpsl_support(symbol)
            
            if "error" in tpsl_info:
                print(f"❌ Error checking TP/SL support: {tpsl_info['error']}")
                return
            
            print(f"\n📊 TP/SL Analysis for {symbol}:")
            print(f"  📈 Status: {tpsl_info['status']}")
            print(f"  💱 Spot Trading: {'✅' if tpsl_info['spotTradingAllowed'] else '❌'}")
            print(f"  📋 All Order Types: {', '.join(tpsl_info['orderTypes'])}")
            print(f"  🎯 TP/SL Order Types: {', '.join(tpsl_info['tpsl_order_types'])}")
            
            # Check current open orders to see if any have TP/SL
            try:
                open_orders = await self.client.get_open_orders(symbol)
                print(f"\n📝 Current Open Orders: {len(open_orders)}")
                
                for order in open_orders:
                    order_id = order.get('orderId', 'Unknown')
                    order_type = order.get('type', 'Unknown')
                    side = order.get('side', 'Unknown')
                    price = order.get('price', 'Unknown')
                    stop_price = order.get('stopPrice', None)
                    
                    print(f"  📄 Order {order_id}: {order_type} {side} @ {price}")
                    if stop_price:
                        print(f"    🛑 Stop Price: {stop_price}")
                    else:
                        print(f"    ⚠️  No stop price found")
                        
            except Exception as e:
                print(f"❌ Error checking open orders: {e}")
            
            return tpsl_info

    async def test_tpsl_order_types(self):
        """Test different TP/SL order type variations to find what works"""
        if not self.client:
            await self.initialize()
        
        async with self.client:
            symbol = self.config.trading_params.symbol
            print(f"🧪 Testing TP/SL order type variations for {symbol}...")
            
            # Test parameters
            test_quantity = 1.0
            test_price = 1.00
            test_stop_price = 0.95
            
            print(f"📋 Test parameters:")
            print(f"  Symbol: {symbol}")
            print(f"  Side: BUY")
            print(f"  Quantity: {test_quantity}")
            print(f"  Price: ${test_price}")
            print(f"  Stop Price: ${test_stop_price}")
            print(f"\n🔍 Testing different order type variations...\n")
            
            results = await self.client.test_tpsl_order_types(
                symbol, 'BUY', test_quantity, test_price, test_stop_price
            )
            
            print(f"📊 Test Results for {symbol}:")
            print(f"  🎯 Successful Method: {results.get('successful_method', 'None found')}")
            print(f"  📝 Methods Tested: {len(results.get('tested_methods', []))}")
            
            if results.get('successful_method'):
                print(f"\n✅ SUCCESS! Found working TP/SL method: {results['successful_method']}")
                
                # Show the successful parameters
                for test in results['tested_methods']:
                    if test['method'] == results['successful_method']:
                        print(f"🔧 Working parameters:")
                        for key, value in test['params'].items():
                            print(f"  {key}: {value}")
                        break
            else:
                print(f"\n❌ No working TP/SL order types found for {symbol}")
            
            print(f"\n📋 Detailed Results:")
            for test in results.get('tested_methods', []):
                status_icon = "✅" if test['status'] == 'success' else "❌"
                print(f"  {status_icon} {test['method']}: {test['status']}")
                if test.get('error'):
                    print(f"    Error: {test['error'][:100]}...")
            
            if results.get('error_details'):
                print(f"\n⚠️  Common errors:")
                for error in results['error_details'][:3]:  # Show first 3 errors
                    print(f"  • {error[:80]}...")
            
            return results

    async def place_bracket_buy_order(self, price: float, quantity: Optional[float] = None, stop_loss_pct: float = 5.0, take_profit_pct: float = 10.0):
        """Place a bracket buy order with both stop-loss and take-profit"""
        if not self.engine:
            await self.initialize()
        
        async with self.client:
            # Validate symbol first
            symbol = self.config.trading_params.symbol
            print(f"🔍 Validating trading symbol: {symbol}")
            
            is_valid = await self.client.validate_symbol(symbol)
            if not is_valid:
                print(f"❌ Symbol '{symbol}' is not valid or not supported for API trading")
                print("💡 Suggestions:")
                print("  • Check the symbol format (should be like BTCUSDT, ETHUSDT)")
                print("  • Use --symbol flag to override (e.g., --symbol BTCUSDT)")
                print("  • Run 'python main.py --action symbols --search BTC' to find valid symbols")
                return None
            
            print(f"✅ Symbol '{symbol}' is valid for trading")
            
            # Calculate and show bracket details
            stop_loss_price = price * (1 - stop_loss_pct / 100)
            take_profit_price = price * (1 + take_profit_pct / 100)
            
            print(f"\n🎯 Bracket Order Details:")
            print(f"  📊 Symbol: {symbol}")
            print(f"  💰 Entry Price: ${price}")
            print(f"  📉 Stop Loss: ${stop_loss_price:.4f} (-{stop_loss_pct}%)")
            print(f"  📈 Take Profit: ${take_profit_price:.4f} (+{take_profit_pct}%)")
            if quantity:
                print(f"  🔢 Quantity: {quantity}")
                total_cost = price * quantity
                max_loss = (price - stop_loss_price) * quantity
                max_profit = (take_profit_price - price) * quantity
                print(f"  💵 Total Cost: ${total_cost:.2f}")
                print(f"  🔻 Max Loss: ${max_loss:.2f}")
                print(f"  🔺 Max Profit: ${max_profit:.2f}")
            
            result = await self.engine.place_bracket_buy_order(price, quantity, stop_loss_pct, take_profit_pct)
            if result:
                logger.info(f"Bracket buy order result: {result}")
                return result
            else:
                logger.warning("Bracket buy order was not placed")
                return None

    async def place_sequential_bracket_buy_order(
        self, 
        entry_price: float, 
        stop_loss_price: float,
        take_profit_price: float,
        quantity: Optional[float] = None
    ):
        """
        Place a sequential bracket buy order:
        1. Place LIMIT buy order at entry_price
        2. Monitor until filled
        3. Automatically place TAKE_PROFIT and STOP_LOSS orders
        
        Example: buy 5 XRP at 1.1 USDT, stop loss at 1.0 USDT, take profit at 2.0 USDT
        """
        if not self.engine:
            await self.initialize()
        
        async with self.client:
            # Validate symbol first
            symbol = self.config.trading_params.symbol
            print(f"🔍 Validating trading symbol: {symbol}")
            
            is_valid = await self.client.validate_symbol(symbol)
            if not is_valid:
                print(f"❌ Symbol '{symbol}' is not valid or not supported for trading")
                print("💡 Suggestions:")
                print("  • Check the symbol format (should be like BTCUSDT, ETHUSDT)")
                print("  • Use --symbol flag to override (e.g., --symbol BTCUSDT)")
                print("  • Run 'python main.py --action symbols --search BTC' to find valid symbols")
                return None
            
            print(f"✅ Symbol '{symbol}' is valid for trading")
            
            # Display sequential bracket order details
            print(f"\n🎯 Sequential Bracket Order Details:")
            print(f"  📊 Symbol: {symbol}")
            print(f"  🔥 Step 1: Place LIMIT BUY order @ ${entry_price}")
            print(f"  ⏳ Step 2: Wait for order to fill")
            print(f"  🛡️  Step 3: Place STOP_LOSS order @ ${stop_loss_price}")
            print(f"  💰 Step 4: Place TAKE_PROFIT order @ ${take_profit_price}")
            
            if quantity:
                print(f"  🔢 Quantity: {quantity}")
                total_cost = entry_price * quantity
                max_loss = (entry_price - stop_loss_price) * quantity
                max_profit = (take_profit_price - entry_price) * quantity
                print(f"  💵 Total Cost: ${total_cost:.2f}")
                print(f"  🔻 Max Loss: ${max_loss:.2f}")
                print(f"  🔺 Max Profit: ${max_profit:.2f}")
                if max_loss > 0:
                    print(f"  📈 Risk/Reward Ratio: 1:{max_profit/max_loss:.2f}")
            
            # Validate price logic
            if stop_loss_price >= entry_price:
                print(f"❌ Error: Stop loss price (${stop_loss_price}) must be below entry price (${entry_price}) for buy orders")
                return None
            
            if take_profit_price <= entry_price:
                print(f"❌ Error: Take profit price (${take_profit_price}) must be above entry price (${entry_price}) for buy orders")
                return None
            
            print(f"\n🚀 Initiating sequential bracket order...")
            
            result = await self.engine.place_sequential_bracket_buy_order(
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                quantity=quantity
            )
            
            if result:
                print(f"\n🔄 Starting position monitoring...")
                print(f"💡 The bot will now monitor your position and manage the protective orders automatically.")
                print(f"📊 Order ID: {result['main_order'].get('orderId', 'Unknown')}")
                print(f"⏳ Press Ctrl+C to stop monitoring and exit")
                
                if not self.config.dry_run:
                    print(f"🚨 LIVE TRADING MODE - Real orders are being monitored!")
                else:
                    print(f"🧪 DRY RUN MODE - This is a simulation")
                
                print()
                logger.info(f"Sequential bracket order placed, starting monitoring...")
                
                # Start the monitoring loop (similar to the "start" action)
                if not self.client or not self.engine:
                    await self.initialize()
                
                self.running = True
                
                async with self.client:
                    # Start position monitoring task
                    monitor_task = asyncio.create_task(self.engine.monitor_positions())
                    
                    try:
                        logger.info("Position monitoring started. Press Ctrl+C to exit.")
                        # Keep the program running while monitoring
                        while self.running:
                            await asyncio.sleep(1)
                            
                    except KeyboardInterrupt:
                        logger.info("Received shutdown signal, stopping monitoring...")
                        self.running = False
                    finally:
                        monitor_task.cancel()
                        try:
                            await monitor_task
                        except asyncio.CancelledError:
                            pass
                        
                        logger.info("Position monitoring stopped")
                        print("\n👋 Monitoring stopped. Your positions may still be active on the exchange.")
                        print("💡 To resume monitoring, run: python main.py --action start")
            else:
                print(f"❌ Failed to place sequential bracket order")
                return None

    async def place_simple_bracket_buy_order(
        self, 
        entry_price: float, 
        stop_loss_price: float,
        take_profit_price: float,
        quantity: Optional[float] = None
    ):
        """Place a simple bracket buy order using MEXC's native SL/TP capabilities"""
        if not self.engine:
            await self.initialize()
        
        async with self.client:
            # Validate symbol first
            symbol = self.config.trading_params.symbol
            print(f"🔍 Validating trading symbol: {symbol}")
            
            is_valid = await self.client.validate_symbol(symbol)
            if not is_valid:
                print(f"❌ Symbol '{symbol}' is not valid or not supported for API trading")
                print("💡 Suggestions:")
                print("  • Check the symbol format (should be like BTCUSDT, ETHUSDT)")
                print("  • Use --symbol flag to override (e.g., --symbol XRPUSDT)")
                print("  • Run 'python main.py --action symbols --search XRP' to find valid symbols")
                return None
            
            print(f"✅ Symbol '{symbol}' is valid for trading")
            
            # Display native bracket order details
            print(f"\n🚀 Native Bracket Order Details:")
            print(f"  📊 Symbol: {symbol}")
            print(f"  💰 Entry Price: ${entry_price}")
            print(f"  🛡️  Stop Loss: ${stop_loss_price}")
            print(f"  💎 Take Profit: ${take_profit_price}")
            print(f"  ✨ MEXC handles all SL/TP logic automatically")
            
            if quantity:
                print(f"  🔢 Quantity: {quantity}")
                total_cost = entry_price * quantity
                max_loss = (entry_price - stop_loss_price) * quantity
                max_profit = (take_profit_price - entry_price) * quantity
                print(f"  💵 Total Cost: ${total_cost:.2f}")
                print(f"  🔻 Max Loss: ${max_loss:.2f}")
                print(f"  🔺 Max Profit: ${max_profit:.2f}")
                if max_loss > 0:
                    print(f"  📈 Risk/Reward Ratio: 1:{max_profit/max_loss:.2f}")
            
            # Validate price logic
            if stop_loss_price >= entry_price:
                print(f"❌ Error: Stop loss price (${stop_loss_price}) must be below entry price (${entry_price}) for buy orders")
                return None
            
            if take_profit_price <= entry_price:
                print(f"❌ Error: Take profit price (${take_profit_price}) must be above entry price (${entry_price}) for buy orders")
                return None
            
            print(f"\n🎯 Placing native MEXC bracket order...")
            
            result = await self.engine.place_simple_bracket_order(
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                quantity=quantity
            )
            
            if result:
                print(f"\n✅ Native bracket order placed successfully!")
                print(f"📊 Bracket Type: {result.get('bracket_type', 'Unknown')}")
                print(f"🎯 Order ID: {result.get('main_order', {}).get('orderId', 'Unknown')}")
                print(f"🚀 MEXC will automatically handle:")
                print(f"  • Stop loss execution at ${stop_loss_price}")
                print(f"  • Take profit execution at ${take_profit_price}")
                print(f"💡 No monitoring needed - everything is exchange-managed!")
                
                if not self.config.dry_run:
                    print(f"🚨 LIVE TRADING MODE - Real orders placed!")
                else:
                    print(f"🧪 DRY RUN MODE - This was a simulation")
                
                logger.info(f"Simple bracket order result: {result}")
                return result
            else:
                print(f"❌ Failed to place native bracket order")
                return None

async def place_simple_bracket_order(args, trading_engine):
    """Place a simple bracket order using MEXC's native SL/TP capabilities"""
    try:
        if not args.price or not args.stop_loss or not args.take_profit:
            logger.error("Simple bracket order requires --price, --stop-loss, and --take-profit")
            return False
        
        logger.info("Placing simple bracket order with native MEXC SL/TP...")
        
        result = await trading_engine.place_simple_bracket_order(
            entry_price=args.price,
            stop_loss_price=args.stop_loss,
            take_profit_price=args.take_profit,
            quantity=args.quantity
        )
        
        if result:
            logger.info("✅ Simple bracket order placed successfully!")
            logger.info("🚀 MEXC will handle all stop loss and take profit execution")
            logger.info("💡 No monitoring needed - everything is automated!")
            return True
        else:
            logger.error("❌ Failed to place simple bracket order")
            return False
            
    except Exception as e:
        logger.error(f"Error placing simple bracket order: {str(e)}")
        return False

async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="MEXC⚡: High-Performance Crypto Trading Bot for MEXC Exchange")
    parser.add_argument("--action", choices=["start", "buy", "sell", "status", "symbols", "validate", "test-permissions", "find-tradeable", "debug-tpsl", "test-tpsl-types", "bracket", "sequential", "simple-bracket"], 
                       default="start", help="Action to perform")
    parser.add_argument("--price", type=float, help="Price for buy/sell orders")
    parser.add_argument("--quantity", type=float, help="Quantity for orders")
    parser.add_argument("--symbol", type=str, help="Trading symbol override")
    parser.add_argument("--search", type=str, help="Search term for symbols")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode")
    parser.add_argument("--config", type=str, help="Configuration file path")
    parser.add_argument("--time", type=str, help="Schedule order for specific time (format: HH:MM or HH:MM:SS)")
    parser.add_argument("--timezone", type=str, default="America/New_York", help="Timezone for scheduled orders (default: America/New_York)")
    parser.add_argument("--stop-loss", type=float, help="Stop-loss price for sequential bracket orders")
    parser.add_argument("--take-profit", type=float, help="Take-profit price for sequential bracket orders")
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        config = load_config()
        
        # Apply command line overrides
        if args.symbol:
            config.trading_params.symbol = args.symbol
        if args.dry_run:
            config.dry_run = True
        
        # Create bot instance
        bot = TradingBot(config)
        
        # Execute requested action
        if args.action == "start":
            await bot.start()
            
        elif args.action == "buy":
            if not args.price:
                print("Error: --price is required for buy orders")
                sys.exit(1)
            
            # Handle scheduled orders
            if args.time:
                print(f"📅 Scheduling BUY order for {args.time} {args.timezone}")
                print(f"💰 Order details: {args.quantity or 'auto'} units at ${args.price}")
                await wait_until_time(args.time, args.timezone)
            
            result = await bot.place_buy_order(args.price, args.quantity)
            print(f"Buy order result: {result}")
            
        elif args.action == "sell":
            if not args.price:
                print("Error: --price is required for sell orders")
                sys.exit(1)
            
            # Handle scheduled orders
            if args.time:
                print(f"📅 Scheduling SELL order for {args.time} {args.timezone}")
                print(f"💰 Order details: {args.quantity or 'auto'} units at ${args.price}")
                await wait_until_time(args.time, args.timezone)
            
            result = await bot.place_sell_order(args.price, args.quantity)
            print(f"Sell order result: {result}")
            
        elif args.action == "status":
            status = await bot.get_status()
            print("Trading Bot Status:")
            for key, value in status.items():
                print(f"  {key}: {value}")
        
        elif args.action == "symbols":
            await bot.search_symbols(args.search)
            
        elif args.action == "validate":
            await bot.validate_current_symbol()
        
        elif args.action == "test-permissions":
            await bot.test_permissions()
        
        elif args.action == "find-tradeable":
            await bot.find_tradeable_symbols()
        
        elif args.action == "debug-tpsl":
            await bot.debug_tpsl_support()
        
        elif args.action == "test-tpsl-types":
            await bot.test_tpsl_order_types()
        
        elif args.action == "bracket":
            if not args.price:
                print("Error: --price is required for bracket orders")
                sys.exit(1)
            
            # Use percentage-based bracket orders (existing functionality)
            stop_loss_pct = getattr(args, 'stop_loss', 5.0)  # Default 5%
            take_profit_pct = getattr(args, 'take_profit', 10.0)  # Default 10%
            
            result = await bot.place_bracket_buy_order(args.price, args.quantity, stop_loss_pct, take_profit_pct)
            print(f"Bracket buy order result: {result}")
        
        elif args.action == "sequential":
            if not args.price:
                print("Error: --price is required for sequential bracket orders")
                sys.exit(1)
            if not args.stop_loss:
                print("Error: --stop-loss price is required for sequential bracket orders")
                print("Example: --stop-loss 1.0")
                sys.exit(1)
            if not args.take_profit:
                print("Error: --take-profit price is required for sequential bracket orders")
                print("Example: --take-profit 5.0")
                sys.exit(1)
            
            print(f"\n🎯 Sequential Bracket Order Request:")
            print(f"  Entry Price: ${args.price}")
            print(f"  Stop Loss: ${args.stop_loss}")
            print(f"  Take Profit: ${args.take_profit}")
            if args.quantity:
                print(f"  Quantity: {args.quantity}")
            
            # Handle scheduled orders for sequential bracket
            if args.time:
                print(f"📅 Scheduling Sequential Bracket order for {args.time} {args.timezone}")
                print(f"💰 Entry: {args.quantity or 'auto'} units at ${args.price}")
                print(f"🛡️ Stop Loss: ${args.stop_loss} | Take Profit: ${args.take_profit}")
                await wait_until_time(args.time, args.timezone)
            
            result = await bot.place_sequential_bracket_buy_order(
                entry_price=args.price,
                stop_loss_price=args.stop_loss,
                take_profit_price=args.take_profit,
                quantity=args.quantity
            )
            
            if result:
                print(f"\n🔄 Starting position monitoring...")
                print(f"💡 The bot will now monitor your position and manage the protective orders automatically.")
                print(f"📊 Order ID: {result['main_order'].get('orderId', 'Unknown')}")
                print(f"⏳ Press Ctrl+C to stop monitoring and exit")
                
                if not config.dry_run:
                    print(f"🚨 LIVE TRADING MODE - Real orders are being monitored!")
                else:
                    print(f"🧪 DRY RUN MODE - This is a simulation")
                
                print()
                logger.info(f"Sequential bracket order placed, starting monitoring...")
                
                # Start the monitoring loop (similar to the "start" action)
                if not bot.client or not bot.engine:
                    await bot.initialize()
                
                bot.running = True
                
                async with bot.client:
                    # Start position monitoring task
                    monitor_task = asyncio.create_task(bot.engine.monitor_positions())
                    
                    try:
                        logger.info("Position monitoring started. Press Ctrl+C to exit.")
                        # Keep the program running while monitoring
                        while bot.running:
                            await asyncio.sleep(1)
                            
                    except KeyboardInterrupt:
                        logger.info("Received shutdown signal, stopping monitoring...")
                        bot.running = False
                    finally:
                        monitor_task.cancel()
                        try:
                            await monitor_task
                        except asyncio.CancelledError:
                            pass
                        
                        logger.info("Position monitoring stopped")
                        print("\n👋 Monitoring stopped. Your positions may still be active on the exchange.")
                        print("💡 To resume monitoring, run: python main.py --action start")
            else:
                print(f"❌ Failed to place sequential bracket order")
                sys.exit(1)
        elif args.action == "simple-bracket":
            if not args.price:
                print("Error: --price is required for simple bracket orders")
                sys.exit(1)
            if not args.stop_loss:
                print("Error: --stop-loss price is required for simple bracket orders")
                print("Example: --stop-loss 1.0")
                sys.exit(1)
            if not args.take_profit:
                print("Error: --take-profit price is required for simple bracket orders")
                print("Example: --take-profit 5.0")
                sys.exit(1)
            
            print(f"\n🚀 Simple Native Bracket Order Request:")
            print(f"  Entry Price: ${args.price}")
            print(f"  Stop Loss: ${args.stop_loss}")
            print(f"  Take Profit: ${args.take_profit}")
            if args.quantity:
                print(f"  Quantity: {args.quantity}")
            print(f"  ✨ Using MEXC's native SL/TP capabilities")
            
            # Handle scheduled orders for simple bracket
            if args.time:
                print(f"📅 Scheduling Simple Bracket order for {args.time} {args.timezone}")
                print(f"💰 Entry: {args.quantity or 'auto'} units at ${args.price}")
                print(f"🛡️ Stop Loss: ${args.stop_loss} | Take Profit: ${args.take_profit}")
                await wait_until_time(args.time, args.timezone)
            
            result = await bot.place_simple_bracket_buy_order(
                entry_price=args.price,
                stop_loss_price=args.stop_loss,
                take_profit_price=args.take_profit,
                quantity=args.quantity
            )
            
            if not result:
                sys.exit(1)
        else:
            logger.error(f"Unknown action: {args.action}")
            return False
    
    except Exception as e:
        logger.error(f"Application error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    
    # Run the main function
    asyncio.run(main()) 