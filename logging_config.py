"""Logging configuration for the MEXC trading bot"""
import os
import sys
from datetime import datetime
from loguru import logger

# Remove any existing handlers
logger.remove()

# Create logs directory if it doesn't exist
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# Set up file logging
log_file = os.path.join(log_dir, f"MEXCL_{datetime.now().strftime('%Y-%m-%d')}.log")

# Global UI instance for log capturing
_ui_instance = None

def set_ui_instance(ui):
    """Set the UI instance for log capturing"""
    global _ui_instance
    _ui_instance = ui

def get_ui_instance():
    """Get the UI instance for log capturing"""
    return _ui_instance

# Add a handler for UI log capturing
def log_to_ui(message):
    """Send log messages to the UI if available"""
    if _ui_instance:
        try:
            # Ensure the message is converted to string and level is extracted properly
            msg_str = str(message.record["message"])
            level_name = message.record["level"].name
            _ui_instance.add_log_message(msg_str, level_name)
        except Exception as e:
            # Don't use logger here to avoid potential recursion
            print(f"Error sending log to UI: {e}", file=sys.stderr)

"""Initialize Loguru logging with default format"""
# Initialize with base configuration
logger = logger.bind(component="SYSTEM")
logger.remove()

# Add a basic console handler for initialization
logger.add(sys.stderr,
          level="INFO",
          format="{time:HH:mm:ss} | {level: <8} | {extra[component]: <10} | {message}")

# Configure default context
logger = logger.bind(component="SYSTEM")

# Add file handler with enhanced format for AI logs
logger.add(log_file, 
          level="INFO",
          rotation="1 day",
          retention="14 days",
          format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[component]: <10} | {message}")

# Add console handler with enhanced format
logger.add(sys.stderr, 
          level="INFO",
          format="{time:HH:mm:ss} | {level: <8} | {extra[component]: <10} | {message}")

# Add context manager for AI logs
class ai_context:
    def __enter__(self):
        return logger.contextualize(component="AI")
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

# Add context manager for UI logs
class ui_context:
    def __enter__(self):
        return logger.contextualize(component="UI")
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

# Add UI handler
logger.add(log_to_ui, 
          level="INFO",
          format="{message}")