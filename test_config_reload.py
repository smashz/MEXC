#!/usr/bin/env python3
import asyncio
import pytest
from config import load_config
from mexc_client import MexcClient
from trading_engine import TradingEngine
import os
import time

@pytest.mark.asyncio
async def test_config_reload():
    """Test configuration reloading when .env changes"""
    config = load_config()
    client = MexcClient(config.credentials)
    engine = TradingEngine(config, client)
    
    # Store initial symbol
    initial_symbol = config.trading_params.symbol
    
    try:
        # Initialize engine
        await engine.initialize()
        
        # Change symbol in .env
        with open('.env', 'r') as f:
            env_content = f.read()
        
        new_symbol = 'XRP_USDT' if initial_symbol != 'XRP_USDT' else 'BTC_USDT'
        updated_content = env_content.replace(f'SYMBOL={initial_symbol}', f'SYMBOL={new_symbol}')
        
        with open('.env', 'w') as f:
            f.write(updated_content)
        
        # Wait a moment for file system
        await asyncio.sleep(1)
        
        # Reload config
        assert await engine.reload_config(), "Config reload should succeed"
        
        # Verify symbol was updated
        assert engine.config.trading_params.symbol == new_symbol, f"Symbol should be updated to {new_symbol}"
        
    finally:
        # Restore original .env content
        with open('.env', 'w') as f:
            f.write(env_content)

if __name__ == "__main__":
    asyncio.run(test_config_reload())