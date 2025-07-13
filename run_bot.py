#!/usr/bin/env python3
"""
Launcher script for MEXC Trading Bot
Provides easy startup with basic configuration validation
"""

import os
import sys
import shlex
import asyncio
import subprocess
from pathlib import Path

def check_requirements():
    """Check if basic requirements are met"""
    errors = []
    
    # Check if .env file exists
    if not os.path.exists('.env'):
        if os.path.exists('env_example.txt'):
            errors.append("Configuration file '.env' not found. Please copy 'env_example.txt' to '.env' and configure your settings.")
        else:
            errors.append("Configuration file '.env' not found. Please create it with your MEXC API credentials.")
    
    # Check if logs directory exists
    if not os.path.exists('logs'):
        print("Creating logs directory...")
        os.makedirs('logs', exist_ok=True)
    
    # Try to import main modules
    try:
        import main
    except ImportError as e:
        errors.append(f"Failed to import main module: {e}")
        errors.append("Please run: pip install -r requirements.txt")
    
    return errors

def print_banner():
    """Print startup banner"""
    banner = """
    ╔═══════════════════════════════════════════════════════════════════════════════════════════════╗
    ║                   MEXC⚡: High-Performance Crypto Trading Bot for MEXC Exchange               ║
    ╚═══════════════════════════════════════════════════════════════════════════════════════════════╝
    """
    print(banner)

def main():
    """Main launcher function"""
    print_banner()
    
    print("🔍 Checking requirements...")
    errors = check_requirements()
    
    if errors:
        print("\n❌ Setup Issues Found:")
        for error in errors:
            print(f"   • {error}")
        print("\nPlease fix these issues before running the bot.")
        sys.exit(1)
    
    print("✅ Requirements check passed!")
    
    # Check if we have API credentials configured
    if os.path.exists('.env'):
        with open('.env', 'r') as f:
            content = f.read()
            if 'your_mexc_api_key_here' in content or not any('MEXC_API_KEY=' in line and len(line.split('=')[1].strip()) > 10 for line in content.split('\n')):
                print("\n⚠️  Warning: Please configure your MEXC API credentials in the .env file")
                print("   Edit .env and replace placeholder values with your actual API credentials")
                
                response = input("\nContinue anyway with current configuration? (y/N): ").lower()
                if response != 'y':
                    print("Exiting. Please configure your API credentials first.")
                    sys.exit(0)
    
    print("\n Starting MEXC Trading Bot...")
    print("   Press Ctrl+C to stop the bot")
    print("   Check logs/ directory for detailed logs\n")
    
    '''
    # Import and run the main bot
    try:
        from main import main as bot_main
        asyncio.run(bot_main())
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Bot error: {e}")
        print("Check the logs for detailed error information")
        sys.exit(1)
    '''

    '''
    # Run the main bot command as a subprocess
    try:
        subprocess.run(
            ["python", "main.py", "--action", "test-permissions"],
            check=True
        )
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Bot execution failed with error: {e}")
        print("Check the logs for detailed error information")
        sys.exit(1)
    '''

    COMMAND_FILE = "runlist.txt"

    try:
        with open(COMMAND_FILE, "r", encoding="utf-8") as f:
            commands = [line.strip() for line in f if line.strip()]

        for i, command_line in enumerate(commands, 1):
            print(f"\n▶️  Running command {i}: {command_line}\n", flush=True)  # force flush
            command = shlex.split(command_line)

            # Run and forward the output live
            subprocess.run(command, check=True)

    except FileNotFoundError:
        print(f"\n❌ Command file '{COMMAND_FILE}' not found", flush=True)
        sys.exit(1)

    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user", flush=True)
        sys.exit(1)

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Command failed with error: {e}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
