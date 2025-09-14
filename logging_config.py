# Set up logging configuration
import sys
from loguru import logger
import os
from datetime import datetime

# Remove default logger
logger.remove()

# Create logs directory if it doesn't exist
os.makedirs("logs", exist_ok=True)

# Get log level from environment or config
log_level = os.getenv("LOG_LEVEL", "INFO").upper()

# Add console handler with color
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level=log_level,
    colorize=True
)

# Add file handler with rotation
log_file = f"logs/MEXC_{datetime.now().strftime('%Y-%m-%d')}.log"
logger.add(
    log_file,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    rotation="1 day",
    retention="30 days",
    level="DEBUG",  # Always log debug to file
    encoding="utf-8",
    catch=True,  # Catch exceptions
    backtrace=True,  # Include backtrace in error logs
    diagnose=True,  # Include variable values in error logs
    enqueue=True  # Thread-safe logging
)

# Log startup information
logger.info("Starting MEXC Trading Bot")
logger.info(f"Log Level: {log_level}")
logger.info(f"Log File: {log_file}")