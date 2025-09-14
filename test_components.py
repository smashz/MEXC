#!/usr/bin/env python3
"""
Component test script for MEXC Trading Bot
Tests each component individually to isolate issues
"""
import asyncio
import sys
from loguru import logger
from config import load_config
from mexc_client import MexcClient
from trading_engine import TradingEngine
from chronos_strategy import ChronosTradingStrategy

async def test_api_connection():
    """Test MEXC API connectivity"""
    logger.info("Testing API connection...")
    config = load_config()
    
    async with MexcClient(config.credentials) as client:
        try:
            info = await client.get_exchange_info()
            logger.info("✓ API connection successful")
            logger.debug("Exchange info: {}", info)
            return True
        except Exception as e:
            logger.error("✗ API connection failed: {}", str(e))
            return False

async def test_chronos_strategy():
    """Test Chronos trading strategy"""
    logger.info("Testing Chronos strategy...")
    config = load_config()
    strategy = ChronosTradingStrategy(config)
    
    try:
        # Test with sample data
        test_prices = [100.0, 101.0, 99.0, 102.0, 103.0]
        for price in test_prices:
            strategy.update_data(price)
        
        prediction = strategy.predict()
        logger.info("✓ Strategy prediction successful")
        logger.debug("Prediction: {}", prediction)
        return True
    except Exception as e:
        logger.error("✗ Strategy test failed: {}", str(e))
        return False

async def test_trading_engine():
    """Test trading engine initialization"""
    logger.info("Testing trading engine...")
    config = load_config()
    
    async with MexcClient(config.credentials) as client:
        try:
            engine = TradingEngine(config, client)
            await engine.initialize()
            logger.info("✓ Trading engine initialization successful")
            logger.debug("Engine state: Historical prices: {}, Last price: {}", 
                        len(engine.historical_prices), engine.last_price)
            return True
        except Exception as e:
            logger.error("✗ Trading engine test failed: {}", str(e))
            return False

async def run_component_tests():
    """Run all component tests"""
    logger.info("Starting component tests...")
    
    results = {
        "API Connection": await test_api_connection(),
        "Chronos Strategy": await test_chronos_strategy(),
        "Trading Engine": await test_trading_engine()
    }
    
    logger.info("\nTest Results:")
    for component, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        logger.info("{}: {}", component, status)
    
    return all(results.values())

if __name__ == "__main__":
    success = asyncio.run(run_component_tests())
    sys.exit(0 if success else 1)