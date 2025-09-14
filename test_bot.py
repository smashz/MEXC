#!/usr/bin/env python3
import asyncio
import pytest
from config import load_config
from mexc_client import MexcClient
from trading_engine import TradingEngine

@pytest.mark.asyncio
async def test_bot_initialization():
    """Test basic bot initialization and API connectivity"""
    config = load_config()
    config.dry_run = True  # Ensure we're in dry run mode
    
    client = MexcClient(config.credentials)
    engine = TradingEngine(config, client)
    
    try:
        # Test initialization
        await engine.initialize()
        assert engine.historical_prices, "Historical prices should be loaded"
        assert engine.chronos is not None, "Chronos strategy should be initialized"
        
        # Test price fetching
        price = await engine.get_current_price(config.trading_params.symbol)
        assert price > 0, "Should get valid current price"
        
        # Test strategy updates
        prediction = await engine.update_strategy()
        assert prediction is not None, "Should get valid prediction"
        
        print("✓ Bot initialization test passed")
        return True
        
    except Exception as e:
        print(f"✗ Bot initialization test failed: {e}")
        return False
    finally:
        await engine.stop()

@pytest.mark.asyncio
async def test_chronos_integration():
    """Test Chronos AI strategy integration"""
    config = load_config()
    config.dry_run = True
    
    client = MexcClient(config.credentials)
    engine = TradingEngine(config, client)
    
    try:
        await engine.initialize()
        
        # Test strategy prediction
        prediction = engine.chronos.get_prediction_info()
        assert 'direction' in prediction, "Prediction should have direction"
        assert 'confidence' in prediction, "Prediction should have confidence"
        
        # Test signal generation
        signal = engine.chronos.generate_signal()
        assert signal in ['BUY', 'SELL', 'NEUTRAL'], "Should generate valid signal"
        
        print("✓ Chronos integration test passed")
        return True
        
    except Exception as e:
        print(f"✗ Chronos integration test failed: {e}")
        return False
    finally:
        await engine.stop()

async def run_tests():
    """Run all smoke tests"""
    print("Running MEXC Trading Bot smoke tests...")
    print("=" * 50)
    
    results = await asyncio.gather(
        test_bot_initialization(),
        test_chronos_integration()
    )
    
    if all(results):
        print("\n✓ All smoke tests passed successfully")
        return 0
    else:
        print("\n✗ Some tests failed")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(run_tests())
    exit(exit_code)