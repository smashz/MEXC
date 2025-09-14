#!/usr/bin/env python3
"""Quick test script for the trading bot"""

import asyncio
import sys
from loguru import logger
from config import load_config
from mexc_client import MexcClient
from trading_engine import TradingEngine

async def test_bot():
    """Run a quick test of the trading bot"""
    config = load_config()
    config.dry_run = True
    config.headless = True
    config.trading_params.symbol = "BTCUSDT"
    
    client = MexcClient(config.credentials)
    engine = TradingEngine(config, client)
    
    async with client:
        try:
            # Test initialization
            await engine.initialize()
            assert engine.historical_prices, "Should have historical prices"
            print("✓ Bot initialization successful")
            
            # Test price fetching
            price = await client.get_klines(config.trading_params.symbol, limit=1)
            assert price and len(price) > 0, "Should get valid klines data"
            print("✓ Price data retrieval successful")
            
            # Test strategy
            prediction = await engine.update_strategy()
            assert prediction is not None, "Should get valid prediction"
            print("✓ Strategy update successful")
            
            # Test trading time windows
            is_trading_time = await engine.is_trading_time()
            print(f"✓ Trading time check successful: {is_trading_time}")
            
            print("\nAll tests passed successfully!")
            return True
            
        except Exception as e:
            print(f"\n✗ Test failed: {str(e)}")
            return False
        finally:
            await engine.stop()

if __name__ == "__main__":
    success = asyncio.run(test_bot())
    sys.exit(0 if success else 1)