#!/usr/bin/env python3
"""
DEPRECATED: This module is deprecated in favor of main.py
Please use main.py with the --headless flag for terminal UI functionality.
This module will be removed in a future version.
"""

import sys
import asyncio
from loguru import logger
from config import load_config
from main import TradingBotUI
from trading_engine import TradingEngine
from mexc_client import MexcClient

logger.warning(
    "botUI.py is deprecated. Please use 'python main.py --action start [--headless]' instead. "
    "This module will be removed in a future version."
)

async def main():
    """Wrapper for backward compatibility"""
    # Parse legacy arguments
    headless_mode = '--headless' in sys.argv
    
    try:
        # Load config
        config = load_config()
        config.headless = headless_mode
        
        # Initialize components
        client = MexcClient(config.credentials)
        engine = TradingEngine(config, client)
        ui = TradingBotUI(engine, headless_mode)
        
        # Start UI
        await ui.start()
        
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())