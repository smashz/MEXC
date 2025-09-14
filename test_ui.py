#!/usr/bin/env python3
import sys
import os
from loguru import logger
import curses
from main import TradingBotUI
from trading_engine import TradingEngine
from config import load_config
import time

def test_logging():
    """Test logging functionality"""
    config = load_config()
    engine = TradingEngine(config)
    ui = TradingBotUI(engine)
    
    # Test different log levels
    logger.debug("Test debug message")
    logger.info("Test info message")
    logger.warning("Test warning message")
    logger.error("Test error message")
    
    # Test log queue management
    for i in range(105):  # Test queue overflow (MAX_LOG_MESSAGES = 100)
        logger.info(f"Test message {i}")
    
    # Verify log file creation
    log_file = f"logs/MEXCL_{time.strftime('%Y-%m-%d')}.log"
    assert os.path.exists(log_file), "Log file was not created"
    
    # Test UI log capture
    assert len(ui.log_messages) <= ui.MAX_LOG_MESSAGES, "Log queue exceeded maximum size"
    
    return True

def test_session_performance():
    """Test session performance tracking"""
    config = load_config()
    engine = TradingEngine(config)
    ui = TradingBotUI(engine)
    
    # Test initial values
    assert ui.session_start_balance == 0.0, "Initial start balance should be 0"
    assert ui.session_current_balance == 0.0, "Initial current balance should be 0"
    
    # Force an update
    ui._update_session_performance()
    
    # Verify balance update
    assert ui.session_start_balance > 0, "Start balance not updated"
    
    return True

def test_ui_display():
    """Test UI display functionality"""
    config = load_config()
    engine = TradingEngine(config)
    ui = TradingBotUI(engine)
    
    try:
        # Initialize curses
        stdscr = curses.initscr()
        curses.start_color()
        curses.use_default_colors()
        
        # Test color pairs
        for name, num in ui.COLORS.items():
            curses.init_pair(num, getattr(curses, f'COLOR_{name.upper()}'), -1)
        
        # Test display update
        ui.stdscr = stdscr
        ui._update_display()
        
        # Clean up
        curses.endwin()
        return True
        
    except Exception as e:
        curses.endwin()
        logger.error(f"UI display test failed: {e}")
        return False

def main():
    """Run all tests"""
    try:
        print("Testing logging system...")
        assert test_logging(), "Logging test failed"
        print("✓ Logging test passed")
        
        print("Testing session performance tracking...")
        assert test_session_performance(), "Session performance test failed"
        print("✓ Session performance test passed")
        
        print("Testing UI display...")
        assert test_ui_display(), "UI display test failed"
        print("✓ UI display test passed")
        
        print("\nAll tests passed successfully!")
        return 0
        
    except AssertionError as e:
        print(f"\nTest failed: {str(e)}")
        return 1
    except Exception as e:
        print(f"\nUnexpected error: {str(e)}")
        return 2

if __name__ == "__main__":
    sys.exit(main())