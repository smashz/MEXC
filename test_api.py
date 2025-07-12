import json
import asyncio
from mexc_client import MexcClient
from config import load_config

async def test_api():
    config = load_config()
    client = MexcClient(config.credentials)
    
    async with client:
        print('=' * 100)
        print('MEXC API TEST RESULTS')
        print('=' * 100)
        
        # Test exchange info
        print('\n📊 TESTING EXCHANGE INFO')
        print('-' * 100)
        try:
            exchange_info = await client.get_exchange_info()
            print(f'\n✓ Response keys: {", ".join(list(exchange_info.keys()))}')
            
            symbols = exchange_info.get('symbols', [])
            print(f'✓ Total symbols: {len(symbols)}')
            
            if symbols:
                first_symbol = symbols[0]
                print(f'\n📈 Symbol Structure Sample:')
                print(f'  Symbol: {first_symbol.get("symbol")}')
                print(f'  Status: {first_symbol.get("status")}')
                
                # Count USDT pairs
                usdt_pairs = [s for s in symbols if s.get('symbol', '').endswith('USDT')]
                print(f'\n💰 Market Distribution:')
                print(f'  USDT pairs: {len(usdt_pairs)}')
                
                # Count all symbols by quote asset
                quote_assets = {}
                for symbol in symbols:
                    symbol_name = symbol.get('symbol', '')
                    for quote in ['USDT', 'BTC', 'ETH', 'BNB', 'USDC']:
                        if symbol_name.endswith(quote):
                            quote_assets[quote] = quote_assets.get(quote, 0) + 1
                            break
                
                for quote, count in quote_assets.items():
                    print(f'  {quote} pairs: {count}')
                
                if usdt_pairs:
                    print(f'\n📋 Sample USDT Trading Pairs:')
                    for i, pair in enumerate(usdt_pairs[:5]):
                        print(f'  {i+1}. {pair.get("symbol")} - {pair.get("status")}')
                        
            # Test if we can get specific symbol info (e.g., XRPUSDT)
            print('\n🔍 TESTING SPECIFIC SYMBOL (XRPUSDT)')
            print('-' * 100)
            try:
                xrp_info = await client.get_exchange_info('XRPUSDT')
                xrp_symbols = xrp_info.get('symbols', [])
                print(f'\n✓ XRPUSDT query successful: {len(xrp_symbols)} symbols returned')
                if xrp_symbols:
                    symbol_data = xrp_symbols[0]
                    print(f'  Symbol: {symbol_data.get("symbol")}')
                    print(f'  Status: {symbol_data.get("status")}')
                    print(f'  Base Asset: {symbol_data.get("baseAsset", "N/A")}')
                    print(f'  Quote Asset: {symbol_data.get("quoteAsset", "N/A")}')
            except Exception as e:
                print(f'\n❌ Error getting XRPUSDT info: {e}')
                
        except Exception as e:
            print(f'\n❌ API Error: {e}')
            print('\nDetailed error information:')
            import traceback
            traceback.print_exc()
        
        print('\n' + '=' * 100)
        print('TEST COMPLETED')
        print('=' * 100)

if __name__ == "__main__":
    asyncio.run(test_api())
