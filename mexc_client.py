import hmac
import time
import json
import asyncio
import aiohttp
import hashlib
import urllib.parse
from typing import Dict, Any, Optional, List
from loguru import logger
from config import MexcCredentials

class MexcClient:
    """High-performance async MEXC API client with rate limiting and error handling"""
    
    BASE_URL = "https://api.mexc.com"
    
    def __init__(self, credentials: MexcCredentials, rate_limit_rps: float = 10.0):
        self.api_key = credentials.api_key
        self.secret_key = credentials.secret_key
        self.rate_limit_rps = rate_limit_rps
        self.session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0
        self._request_count = 0
        self._rate_limit_lock = asyncio.Lock()
        
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    def _generate_signature(self, query_string: str) -> str:
        """Generate HMAC SHA256 signature for API requests"""
        return hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    async def _rate_limit(self):
        """Implement rate limiting to stay within API limits"""
        async with self._rate_limit_lock:
            now = time.time()
            time_since_last = now - self._last_request_time
            min_interval = 1.0 / self.rate_limit_rps
            
            if time_since_last < min_interval:
                sleep_time = min_interval - time_since_last
                await asyncio.sleep(sleep_time)
            
            self._last_request_time = time.time()
            self._request_count += 1
    
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        params: Optional[Dict[str, Any]] = None,
        signed: bool = True
    ) -> Dict[str, Any]:
        """Make authenticated API request with rate limiting"""
        await self._rate_limit()
        
        if params is None:
            params = {}
        
        # Add timestamp for signed requests
        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 60000  # 60 second receive window
        
        # Create query string
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        
        headers = {
            'X-MEXC-APIKEY': self.api_key,
            'Content-Type': 'application/json'
        }
        
        # Add signature for signed requests
        if signed:
            signature = self._generate_signature(query_string)
            query_string += f"&signature={signature}"
        
        url = f"{self.BASE_URL}{endpoint}"
        if query_string:
            url += f"?{query_string}"
        
        try:
            logger.debug(f"Making {method} request to {endpoint}")
            async with self.session.request(method, url, headers=headers) as response:
                response_text = await response.text()
                
                if response.status == 200:
                    return json.loads(response_text)
                else:
                    logger.error(f"API Error {response.status}: {response_text}")
                    
                    # Provide more helpful error messages
                    if response.status == 400:
                        try:
                            error_data = json.loads(response_text)
                            if error_data.get('code') == 10007:
                                logger.error("Symbol not supported. Use get_exchange_info() to see available symbols.")
                        except:
                            pass
                    
                    raise Exception(f"MEXC API Error {response.status}: {response_text}")
                    
        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            raise
    
    async def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get exchange information and available symbols"""
        params = {}
        if symbol:
            params['symbol'] = symbol
        
        try:
            # Try the standard endpoint first
            result = await self._make_request('GET', '/api/v3/exchangeInfo', params, signed=False)
            logger.info(f"Exchange info returned {len(result.get('symbols', []))} symbols")
            return result
        except Exception as e:
            logger.warning(f"Standard exchangeInfo failed: {e}")
            
            # Try alternative endpoints
            try:
                # Try with symbols parameter as array
                if symbol:
                    params['symbols'] = f'["{symbol}"]'
                    del params['symbol']
                result = await self._make_request('GET', '/api/v3/exchangeInfo', params, signed=False)
                logger.info(f"Alternative exchange info returned {len(result.get('symbols', []))} symbols")
                return result
            except Exception as e2:
                logger.error(f"Alternative exchangeInfo also failed: {e2}")
                raise e
    
    async def get_all_symbols(self) -> List[str]:
        """Get list of all available trading symbols that allow spot trading"""
        try:
            # Try multiple approaches to get symbols
            exchange_info = await self.get_exchange_info()
            symbols = []
            
            for symbol_info in exchange_info.get('symbols', []):
                symbol_name = symbol_info.get('symbol', '')
                status = symbol_info.get('status', '')
                is_spot_trading_allowed = symbol_info.get('isSpotTradingAllowed', False)
                
                # Check for various status indicators that mean trading is allowed
                status_ok = status in ['TRADING', 'ENABLED', 'ACTIVE', 1, '1']
                
                # Only include symbols that allow spot trading
                if status_ok and is_spot_trading_allowed:
                    symbols.append(symbol_name)
            
            if not symbols:
                logger.warning("No symbols found with standard filters, trying alternative approach")
                # If no symbols with standard status, try all symbols but still check spot trading
                for symbol_info in exchange_info.get('symbols', []):
                    symbol_name = symbol_info.get('symbol', '')
                    is_spot_trading_allowed = symbol_info.get('isSpotTradingAllowed', False)
                    if symbol_name and is_spot_trading_allowed:
                        symbols.append(symbol_name)
            
            logger.info(f"Found {len(symbols)} tradeable symbols (with spot trading enabled)")
            return sorted(symbols)
            
        except Exception as e:
            logger.error(f"Failed to get symbols: {str(e)}")
            return []
    
    async def search_symbols(self, search_term: str) -> List[str]:
        """Search for symbols containing the search term"""
        all_symbols = await self.get_all_symbols()
        search_term_upper = search_term.upper()
        matching_symbols = [symbol for symbol in all_symbols if search_term_upper in symbol.upper()]
        logger.info(f"Found {len(matching_symbols)} symbols matching '{search_term}'")
        return matching_symbols
    
    async def validate_symbol(self, symbol: str) -> bool:
        """Check if a symbol is valid and tradable"""
        try:
            exchange_info = await self.get_exchange_info(symbol)
            symbols = exchange_info.get('symbols', [])
            for symbol_info in symbols:
                if symbol_info.get('symbol') == symbol:
                    status = symbol_info.get('status', '')
                    is_spot_trading_allowed = symbol_info.get('isSpotTradingAllowed', False)
                    
                    # Check both status and spot trading permission
                    status_ok = status in ['TRADING', 'ENABLED', 'ACTIVE', 1, '1']
                    
                    logger.info(f"Symbol {symbol} validation: status={status}, spotTradingAllowed={is_spot_trading_allowed}")
                    
                    return status_ok and is_spot_trading_allowed
            return False
        except Exception as e:
            logger.error(f"Failed to validate symbol {symbol}: {str(e)}")
            return False
    
    async def get_server_time(self) -> Dict[str, Any]:
        """Get server time - useful for testing connectivity"""
        return await self._make_request('GET', '/api/v3/time', {}, signed=False)
    
    async def test_connectivity(self) -> bool:
        """Test API connectivity"""
        try:
            result = await self._make_request('GET', '/api/v3/ping', {}, signed=False)
            logger.info("API connectivity test successful")
            return True
        except Exception as e:
            logger.error(f"API connectivity test failed: {e}")
            return False

    async def test_api_permissions(self) -> Dict[str, Any]:
        """Test API key permissions and account status"""
        permissions = {
            "connectivity": False,
            "account_access": False,
            "trading_enabled": False,
            "account_type": "unknown",
            "trading_status": "unknown",
            "error_details": []
        }
        
        try:
            # Test basic connectivity
            ping_result = await self._make_request('GET', '/api/v3/ping', {}, signed=False)
            permissions["connectivity"] = True
            logger.info("✅ API connectivity successful")
        except Exception as e:
            permissions["error_details"].append(f"Connectivity failed: {e}")
            logger.error(f"❌ API connectivity failed: {e}")
            return permissions
        
        try:
            # Test account access
            account_info = await self._make_request('GET', '/api/v3/account')
            permissions["account_access"] = True
            permissions["account_type"] = account_info.get("accountType", "unknown")
            logger.info("✅ Account access successful")
            logger.info(f"Account type: {permissions['account_type']}")
            
            # Check if account can trade
            can_trade = account_info.get("canTrade", False)
            can_withdraw = account_info.get("canWithdraw", False)
            can_deposit = account_info.get("canDeposit", False)
            
            permissions["trading_enabled"] = can_trade
            permissions["trading_status"] = f"Trade: {can_trade}, Withdraw: {can_withdraw}, Deposit: {can_deposit}"
            
            if can_trade:
                logger.info("✅ Trading permissions enabled")
            else:
                logger.error("❌ Trading permissions disabled")
                permissions["error_details"].append("Account trading is disabled")
            
            # Check balances
            balances = account_info.get("balances", [])
            usdt_balance = None
            for balance in balances:
                if balance.get("asset") == "USDT":
                    usdt_balance = float(balance.get("free", 0))
                    break
            
            if usdt_balance is not None:
                logger.info(f"USDT balance: {usdt_balance}")
                if usdt_balance < 1:  # Minimum for testing
                    permissions["error_details"].append(f"Low USDT balance: {usdt_balance}")
            
        except Exception as e:
            permissions["error_details"].append(f"Account access failed: {e}")
            logger.error(f"❌ Account access failed: {e}")
        
        return permissions

    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information"""
        return await self._make_request('GET', '/api/v3/account')
    
    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get symbol information"""
        params = {'symbol': symbol}
        return await self._make_request('GET', '/api/v3/exchangeInfo', params, signed=False)
    
    async def get_ticker_price(self, symbol: str) -> Dict[str, Any]:
        """Get current ticker price"""
        params = {'symbol': symbol}
        return await self._make_request('GET', '/api/v3/ticker/price', params, signed=False)
    
    async def place_limit_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float, 
        price: float,
        time_in_force: str = 'GTC'
    ) -> Dict[str, Any]:
        """Place a limit order (BUY or SELL)"""
        # Store symbol for quantity formatting
        self._current_symbol = symbol
        
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'timeInForce': time_in_force,
            'quantity': quantity,
            'price': price
        }
        
        # Debug logging for troubleshooting
        logger.info(f"Placing {side} order with parameters:")
        logger.info(f"  Symbol: {symbol}")
        logger.info(f"  Side: {side}")
        logger.info(f"  Type: LIMIT")
        logger.info(f"  Quantity: {quantity}")
        logger.info(f"  Price: {price}")
        logger.info(f"  TimeInForce: {time_in_force}")
        
        try:
            result = await self._make_request('POST', '/api/v3/order', params)
            logger.info(f"Order placed successfully: {result}")
            return result
        except Exception as e:
            # Enhanced error handling for common issues
            error_str = str(e)
            if "10007" in error_str and "symbol not support api" in error_str:
                logger.error("Error 10007: Symbol not supported for API trading")
                logger.error("This could be due to:")
                logger.error("  • API key lacks trading permissions")
                logger.error("  • Symbol is restricted for your account region")
                logger.error("  • Account verification level insufficient")
                logger.error("  • Using spot trading symbols on futures API or vice versa")
                
                # Try to get more detailed symbol info
                try:
                    logger.info("Checking detailed symbol information...")
                    symbol_details = await self.get_exchange_info(symbol)
                    if symbol_details.get('symbols'):
                        symbol_info = symbol_details['symbols'][0]
                        logger.info(f"Symbol details: {symbol_info}")
                        
                        # Check permissions and filters
                        permissions = symbol_info.get('permissions', [])
                        status = symbol_info.get('status', '')
                        logger.info(f"Symbol permissions: {permissions}")
                        logger.info(f"Symbol status: {status}")
                        
                        if 'SPOT' not in permissions:
                            logger.error("Symbol does not have SPOT trading permission")
                        if status != 'TRADING':
                            logger.error(f"Symbol status is '{status}', not 'TRADING'")
                        
                except Exception as detail_error:
                    logger.error(f"Could not get detailed symbol info: {detail_error}")
            
            raise
    
    async def place_limit_order_with_stop_loss(
        self, 
        symbol: str, 
        side: str, 
        quantity: float, 
        price: float,
        stop_price: float,
        time_in_force: str = 'GTC'
    ) -> Dict[str, Any]:
        """Place a limit order with integrated stop-loss using MEXC's correct API format"""
        
        # MEXC API approach 1: LIMIT with stopPrice + workingType (confirmed working?)
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'timeInForce': time_in_force,
            'quantity': quantity,
            'price': price,
            'stopPrice': stop_price,
            'workingType': 'MARK_PRICE',
            'priceProtect': 'true'
        }
        
        logger.info(f"Placing LIMIT order with integrated stop-loss (MEXC format):")
        logger.info(f"  Symbol: {symbol}")
        logger.info(f"  Side: {side}")
        logger.info(f"  Type: LIMIT with stopPrice")
        logger.info(f"  Quantity: {quantity}")
        logger.info(f"  Limit Price: {price}")
        logger.info(f"  Stop Price: {stop_price}")
        logger.info(f"  Working Type: MARK_PRICE")
        logger.info(f"  Price Protect: true")
        logger.info(f"  TimeInForce: {time_in_force}")
        
        try:
            result = await self._make_request('POST', '/api/v3/order', params)
            logger.info(f"LIMIT order with integrated stop-loss placed successfully: {result}")
            return result
        except Exception as e:
            error_str = str(e)
            logger.warning(f"Primary LIMIT+stopPrice method failed: {error_str}")
            
            # MEXC API approach 2: Try with OCO (One-Cancels-Other) order
            try:
                logger.info("Trying OCO (One-Cancels-Other) order approach...")
                
                # For OCO orders, we need to place a limit order with a stop-loss order
                oco_params = {
                    'symbol': symbol,
                    'side': side,
                    'quantity': quantity,
                    'price': price,  # Limit order price
                    'stopPrice': stop_price,  # Stop-loss trigger price
                    'stopLimitPrice': stop_price * 0.995 if side == 'SELL' else stop_price * 1.005,  # Stop-loss execution price
                    'stopLimitTimeInForce': 'GTC',
                    'type': 'OCO'
                }
                
                logger.info(f"Placing OCO order with parameters: {oco_params}")
                result = await self._make_request('POST', '/api/v3/order/oco', oco_params)
                logger.info(f"OCO order placed successfully: {result}")
                return result
                
            except Exception as oco_e:
                logger.warning(f"OCO order also failed: {oco_e}")
                
                # MEXC API approach 3: Try standard order with additional TP/SL parameters
                try:
                    logger.info("Trying standard order with TP/SL parameters...")
                    
                    standard_params = {
                        'symbol': symbol,
                        'side': side,
                        'type': 'LIMIT',
                        'timeInForce': time_in_force,
                        'quantity': quantity,
                        'price': price,
                        'stopPrice': stop_price,  # Stop-loss trigger
                        'workingType': 'MARK_PRICE',  # Use mark price for stop-loss
                        'priceProtect': 'true'  # Enable price protection
                    }
                    
                    logger.info(f"Placing standard order with TP/SL: {standard_params}")
                    result = await self._make_request('POST', '/api/v3/order', standard_params)
                    logger.info(f"Standard order with TP/SL placed successfully: {result}")
                    return result
                    
                except Exception as standard_e:
                    logger.error(f"All TP/SL integration methods failed. Falling back to regular limit order.")
                    logger.error(f"Standard method error: {standard_e}")
                    
                    # Final fallback: place regular limit order
                    fallback_params = {
                        'symbol': symbol,
                        'side': side,
                        'type': 'LIMIT',
                        'timeInForce': time_in_force,
                        'quantity': quantity,
                        'price': price
                    }
                    
                    try:
                        result = await self._make_request('POST', '/api/v3/order', fallback_params)
                        logger.info(f"Fallback limit order placed (stop-loss will be software-based): {result}")
                        # Add flags to indicate this order needs software stop-loss monitoring
                        result['needs_software_stop_loss'] = True
                        result['stop_price'] = stop_price
                        return result
                    except Exception as fallback_e:
                        logger.error(f"Even fallback limit order failed: {fallback_e}")
                        raise fallback_e

    async def place_stop_loss_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float, 
        stop_price: float,
        limit_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Place a standalone stop loss order (fallback method)"""
        
        # Try STOP_LOSS_LIMIT first
        if limit_price:
            params = {
                'symbol': symbol,
                'side': side,
                'type': 'STOP_LOSS_LIMIT',
                'quantity': quantity,
                'stopPrice': stop_price,
                'price': limit_price,
                'timeInForce': 'GTC'
            }
            order_type_name = 'STOP_LOSS_LIMIT'
        else:
            # Use STOP_LOSS (market order triggered at stop price)
            params = {
                'symbol': symbol,
                'side': side,
                'type': 'STOP_LOSS',
                'quantity': quantity,
                'stopPrice': stop_price,
                'timeInForce': 'GTC'
            }
            order_type_name = 'STOP_LOSS'
        
        logger.info(f"Placing {order_type_name} order with parameters:")
        logger.info(f"  Symbol: {symbol}")
        logger.info(f"  Side: {side}")
        logger.info(f"  Type: {order_type_name}")
        logger.info(f"  Quantity: {quantity}")
        logger.info(f"  Stop Price: {stop_price}")
        if limit_price:
            logger.info(f"  Limit Price: {limit_price}")
        
        try:
            result = await self._make_request('POST', '/api/v3/order', params)
            logger.info(f"{order_type_name} order placed successfully: {result}")
            return result
        except Exception as e:
            error_str = str(e)
            logger.error(f"{order_type_name} order failed: {error_str}")
            raise e

    async def place_market_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float
    ) -> Dict[str, Any]:
        """Place a market order (immediate execution at current market price)"""
        # Store symbol for quantity formatting
        self._current_symbol = symbol
        
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': quantity
        }
        
        logger.info(f"Placing MARKET order: {side} {quantity} {symbol}")
        
        try:
            result = await self._make_request('POST', '/api/v3/order', params)
            logger.info(f"Market order placed successfully: {result}")
            return result
        except Exception as e:
            logger.error(f"Market order failed: {str(e)}")
            raise
    
    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancel an existing order"""
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        return await self._make_request('DELETE', '/api/v3/order', params)
    
    async def get_order_status(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Get order status"""
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        return await self._make_request('GET', '/api/v3/order', params)
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open orders"""
        params = {}
        if symbol:
            params['symbol'] = symbol
        return await self._make_request('GET', '/api/v3/openOrders', params)
    
    async def get_order_history(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        """Get order history"""
        params = {
            'symbol': symbol,
            'limit': limit
        }
        return await self._make_request('GET', '/api/v3/allOrders', params)
    
    async def get_tradeable_usdt_pairs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get USDT pairs that allow spot trading"""
        try:
            exchange_info = await self.get_exchange_info()
            tradeable_pairs = []
            
            for symbol_info in exchange_info.get('symbols', []):
                symbol_name = symbol_info.get('symbol', '')
                quote_asset = symbol_info.get('quoteAsset', '')
                is_spot_trading_allowed = symbol_info.get('isSpotTradingAllowed', False)
                status = symbol_info.get('status', '')
                
                # Look for USDT pairs that allow spot trading
                if (quote_asset == 'USDT' and 
                    is_spot_trading_allowed and 
                    status in ['TRADING', 'ENABLED', 'ACTIVE', 1, '1']):
                    
                    tradeable_pairs.append({
                        'symbol': symbol_name,
                        'baseAsset': symbol_info.get('baseAsset', ''),
                        'status': status,
                        'orderTypes': symbol_info.get('orderTypes', []),
                        'minQuantity': symbol_info.get('baseSizePrecision', ''),
                        'maxQuoteAmount': symbol_info.get('maxQuoteAmount', '')
                    })
            
            # Sort by symbol name and limit results
            tradeable_pairs.sort(key=lambda x: x['symbol'])
            logger.info(f"Found {len(tradeable_pairs)} tradeable USDT pairs")
            
            return tradeable_pairs[:limit]
            
        except Exception as e:
            logger.error(f"Failed to get tradeable USDT pairs: {e}")
            return []
    
    async def check_symbol_tpsl_support(self, symbol: str) -> Dict[str, Any]:
        """Check what TP/SL features are supported for a symbol"""
        try:
            exchange_info = await self.get_exchange_info(symbol)
            if not exchange_info.get('symbols'):
                return {"error": "Symbol not found"}
            
            symbol_info = exchange_info['symbols'][0]
            
            # Extract relevant TP/SL information
            tpsl_info = {
                "symbol": symbol,
                "status": symbol_info.get('status'),
                "orderTypes": symbol_info.get('orderTypes', []),
                "spotTradingAllowed": symbol_info.get('isSpotTradingAllowed', False),
                "filters": []
            }
            
            # Check for relevant filters
            for filter_info in symbol_info.get('filters', []):
                filter_type = filter_info.get('filterType', '')
                if filter_type in ['PRICE_FILTER', 'LOT_SIZE', 'MIN_NOTIONAL', 'PERCENT_PRICE']:
                    tpsl_info["filters"].append(filter_info)
            
            logger.info(f"TP/SL Support Analysis for {symbol}:")
            logger.info(f"  Order Types: {tpsl_info['orderTypes']}")
            logger.info(f"  Spot Trading: {tpsl_info['spotTradingAllowed']}")
            logger.info(f"  Status: {tpsl_info['status']}")
            
            # Check for specific TP/SL related order types
            tpsl_order_types = []
            for order_type in tpsl_info['orderTypes']:
                if any(keyword in order_type for keyword in ['STOP', 'OCO', 'LIMIT']):
                    tpsl_order_types.append(order_type)
            
            tpsl_info["tpsl_order_types"] = tpsl_order_types
            logger.info(f"  TP/SL Related Order Types: {tpsl_order_types}")
            
            return tpsl_info
            
        except Exception as e:
            logger.error(f"Failed to check TP/SL support for {symbol}: {e}")
            return {"error": str(e)}

    async def test_tpsl_order_types(self, symbol: str, side: str, quantity: float, price: float, stop_price: float) -> Dict[str, Any]:
        """Test different TP/SL order type combinations to find what works with MEXC"""
        
        results = {
            "symbol": symbol,
            "tested_methods": [],
            "successful_method": None,
            "error_details": []
        }
        
        # List of different TP/SL order type variations to test
        test_methods = [
            # Method 1: TP_SL_LIMIT order type
            {
                "name": "TP_SL_LIMIT",
                "params": {
                    'symbol': symbol,
                    'side': side,
                    'type': 'TP_SL_LIMIT',
                    'quantity': quantity,
                    'price': price,
                    'stopPrice': stop_price,
                    'timeInForce': 'GTC'
                }
            },
            
            # Method 2: STOP_LOSS_LIMIT order type
            {
                "name": "STOP_LOSS_LIMIT", 
                "params": {
                    'symbol': symbol,
                    'side': side,
                    'type': 'STOP_LOSS_LIMIT',
                    'quantity': quantity,
                    'price': price,
                    'stopPrice': stop_price,
                    'timeInForce': 'GTC'
                }
            },
            
            # Method 3: LIMIT with stopLimitPrice
            {
                "name": "LIMIT_with_stopLimitPrice",
                "params": {
                    'symbol': symbol,
                    'side': side,
                    'type': 'LIMIT',
                    'quantity': quantity,
                    'price': price,
                    'stopLimitPrice': stop_price,
                    'timeInForce': 'GTC'
                }
            },
            
            # Method 4: TAKE_PROFIT_LIMIT
            {
                "name": "TAKE_PROFIT_LIMIT",
                "params": {
                    'symbol': symbol,
                    'side': side,
                    'type': 'TAKE_PROFIT_LIMIT',
                    'quantity': quantity,
                    'price': price,
                    'stopPrice': stop_price,
                    'timeInForce': 'GTC'
                }
            },
            
            # Method 5: LIMIT with additional SL parameters
            {
                "name": "LIMIT_with_SL_params",
                "params": {
                    'symbol': symbol,
                    'side': side,
                    'type': 'LIMIT',
                    'quantity': quantity,
                    'price': price,
                    'stopLossPrice': stop_price,
                    'stopLossType': 'LIMIT',
                    'timeInForce': 'GTC'
                }
            },
            
            # Method 6: CONDITIONAL_LIMIT
            {
                "name": "CONDITIONAL_LIMIT",
                "params": {
                    'symbol': symbol,
                    'side': side,
                    'type': 'CONDITIONAL_LIMIT',
                    'quantity': quantity,
                    'price': price,
                    'triggerPrice': stop_price,
                    'timeInForce': 'GTC'
                }
            }
        ]
        
        logger.info(f"Testing {len(test_methods)} different TP/SL order type methods for {symbol}")
        
        for method in test_methods:
            method_name = method["name"]
            params = method["params"]
            
            try:
                logger.info(f"Testing method: {method_name}")
                logger.info(f"  Parameters: {params}")
                
                # DRY RUN - just test if the API accepts the parameters
                # Add a flag to indicate this is a test
                test_result = await self._test_order_parameters(params)
                
                results["tested_methods"].append({
                    "method": method_name,
                    "params": params,
                    "status": "success" if test_result else "failed",
                    "result": test_result
                })
                
                if test_result and not results["successful_method"]:
                    results["successful_method"] = method_name
                    logger.info(f"✅ SUCCESS: {method_name} method appears to work!")
                    
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"❌ Method {method_name} failed: {error_msg}")
                
                results["tested_methods"].append({
                    "method": method_name,
                    "params": params,
                    "status": "error",
                    "error": error_msg
                })
                
                results["error_details"].append(f"{method_name}: {error_msg}")
        
        return results
    
    async def _test_order_parameters(self, params: Dict[str, Any]) -> bool:
        """Test order parameters without actually placing the order"""
        try:
            # Add test flag to avoid actually placing the order
            test_params = params.copy()
            test_params['test'] = 'true'
            
            # Try the test endpoint first
            try:
                result = await self._make_request('POST', '/api/v3/order/test', test_params)
                logger.info("Order test endpoint succeeded")
                return True
            except Exception as test_e:
                logger.debug(f"Test endpoint failed: {test_e}")
                
                # If test endpoint doesn't exist, try to validate by checking the error message
                # We'll make a real request but cancel it immediately if it succeeds
                try:
                    # Remove test flag and try real endpoint
                    real_params = params.copy()
                    if 'test' in real_params:
                        del real_params['test']
                    
                    # This might actually place an order, so we should be careful
                    # For now, let's just return False to be safe
                    logger.debug("Cannot safely test without test endpoint")
                    return False
                    
                except Exception as real_e:
                    error_msg = str(real_e).lower()
                    
                    # Check if the error indicates the order type is invalid
                    if any(indicator in error_msg for indicator in [
                        'invalid type', 'unsupported type', 'unknown type',
                        'invalid order type', 'order type not supported'
                    ]):
                        return False
                    
                    # If it's a different error (like insufficient balance), the order type might be valid
                    if any(indicator in error_msg for indicator in [
                        'insufficient', 'balance', 'minimum', 'precision'
                    ]):
                        logger.info("Order type appears valid (got balance/precision error)")
                        return True
                    
                    return False
                    
        except Exception as e:
            logger.debug(f"Parameter test failed: {e}")
            return False

    async def place_bracket_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float, 
        price: float,
        stop_loss_percentage: float,
        take_profit_percentage: float,
        time_in_force: str = 'GTC'
    ) -> Dict[str, Any]:
        """Place a bracket order with both stop-loss and take-profit"""
        
        # Calculate stop-loss and take-profit prices
        if side == 'BUY':
            stop_loss_price = price * (1 - stop_loss_percentage / 100)
            take_profit_price = price * (1 + take_profit_percentage / 100)
        else:  # SELL
            stop_loss_price = price * (1 + stop_loss_percentage / 100)
            take_profit_price = price * (1 - take_profit_percentage / 100)
        
        logger.info(f"Placing bracket order ({side}):")
        logger.info(f"  Symbol: {symbol}")
        logger.info(f"  Quantity: {quantity}")
        logger.info(f"  Entry Price: {price}")
        logger.info(f"  Stop Loss: {stop_loss_price} ({stop_loss_percentage}%)")
        logger.info(f"  Take Profit: {take_profit_price} ({take_profit_percentage}%)")
        
        # Method 1: Try MEXC's bracket order (supported?)
        bracket_params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'timeInForce': time_in_force,
            'quantity': quantity,
            'price': price,
            'stopPrice': stop_loss_price,
            'takeProfitPrice': take_profit_price,
            'workingType': 'MARK_PRICE',
            'priceProtect': 'true'
        }
        
        try:
            logger.info("Attempting bracket order with both TP and SL...")
            result = await self._make_request('POST', '/api/v3/order', bracket_params)
            logger.info(f"Bracket order placed successfully: {result}")
            return {
                'main_order': result,
                'stop_loss_price': stop_loss_price,
                'take_profit_price': take_profit_price,
                'bracket_type': 'integrated'
            }
        except Exception as e:
            logger.warning(f"Integrated bracket order failed: {e}")
            
            # Method 2: Try OCO order
            try:
                logger.info("Trying OCO bracket order...")
                oco_params = {
                    'symbol': symbol,
                    'side': side,
                    'quantity': quantity,
                    'price': price,
                    'stopPrice': stop_loss_price,
                    'stopLimitPrice': stop_loss_price,
                    'stopLimitTimeInForce': 'GTC',
                    'takeProfitPrice': take_profit_price,
                    'takeProfitLimitPrice': take_profit_price,
                    'takeProfitTimeInForce': 'GTC',
                    'type': 'OCO'
                }
                
                result = await self._make_request('POST', '/api/v3/order/oco', oco_params)
                logger.info(f"OCO bracket order placed successfully: {result}")
                return {
                    'main_order': result,
                    'stop_loss_price': stop_loss_price,
                    'take_profit_price': take_profit_price,
                    'bracket_type': 'oco'
                }
            except Exception as oco_e:
                logger.warning(f"OCO bracket order failed: {oco_e}")
                
                # Method 3: Place main order and set up software-based TP/SL monitoring
                logger.info("Falling back to software-based bracket monitoring...")
                
                # Place the main order first
                main_params = {
                    'symbol': symbol,
                    'side': side,
                    'type': 'LIMIT',
                    'timeInForce': time_in_force,
                    'quantity': quantity,
                    'price': price
                }
                
                try:
                    main_result = await self._make_request('POST', '/api/v3/order', main_params)
                    logger.info(f"Main order placed, will monitor with software TP/SL: {main_result}")
                    
                    # Add monitoring flags
                    main_result.update({
                        'needs_software_bracket': True,
                        'stop_loss_price': stop_loss_price,
                        'take_profit_price': take_profit_price,
                        'stop_loss_percentage': stop_loss_percentage,
                        'take_profit_percentage': take_profit_percentage,
                        'bracket_type': 'software'
                    })
                    
                    return {
                        'main_order': main_result,
                        'stop_loss_price': stop_loss_price,
                        'take_profit_price': take_profit_price,
                        'bracket_type': 'software'
                    }
                    
                except Exception as main_e:
                    logger.error(f"Even main order failed: {main_e}")
                    raise main_e 

    async def place_stop_loss_market_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float, 
        stop_price: float,
        time_in_force: str = 'GTC'
    ) -> Dict[str, Any]:
        """Place a stop loss market order using MEXC's STOP_LOSS order type"""
        
        # For MEXC, STOP_LOSS orders need both stopPrice and price parameters
        # The price should be slightly worse than stopPrice to ensure execution
        if side == 'SELL':
            # For sell stop loss, price should be slightly below stop price
            limit_price = stop_price * 0.999  # 0.1% below stop price
        else:
            # For buy stop loss, price should be slightly above stop price  
            limit_price = stop_price * 1.001  # 0.1% above stop price
        
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'STOP_LOSS_LIMIT',  # Use STOP_LOSS_LIMIT instead of STOP_LOSS
            'timeInForce': time_in_force,
            'quantity': quantity,
            'stopPrice': stop_price,
            'price': limit_price,  # Required by MEXC
        }
        
        try:
            logger.info(f"Placing STOP_LOSS_LIMIT order: {symbol} {side} {quantity} @ stop price {stop_price}, limit price {limit_price}")
            result = await self._make_request('POST', '/api/v3/order', params)
            logger.info(f"Stop loss order placed: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to place stop loss order: {str(e)}")
            
            # Try with a simpler approach - regular limit order at stop price
            try:
                logger.info("Trying regular LIMIT order as stop loss fallback...")
                params_fallback = {
                    'symbol': symbol,
                    'side': side,
                    'type': 'LIMIT',
                    'timeInForce': time_in_force,
                    'quantity': quantity,
                    'price': stop_price,
                }
                result = await self._make_request('POST', '/api/v3/order', params_fallback)
                logger.info(f"Fallback limit order placed as stop loss: {result}")
                return result
            except Exception as e2:
                logger.error(f"Stop loss fallback also failed: {str(e2)}")
                raise e

    async def place_take_profit_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float, 
        stop_price: float,
        time_in_force: str = 'GTC'
    ) -> Dict[str, Any]:
        """Place a take profit order using MEXC's TAKE_PROFIT order type"""
        
        # For MEXC, TAKE_PROFIT orders need both stopPrice and price parameters
        # The price should be slightly worse than stopPrice to ensure execution
        if side == 'SELL':
            # For sell take profit, price should be slightly below stop price
            limit_price = stop_price * 0.999  # 0.1% below stop price
        else:
            # For buy take profit, price should be slightly above stop price
            limit_price = stop_price * 1.001  # 0.1% above stop price
        
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'TAKE_PROFIT_LIMIT',  # Use TAKE_PROFIT_LIMIT instead of TAKE_PROFIT
            'timeInForce': time_in_force,
            'quantity': quantity,
            'stopPrice': stop_price,
            'price': limit_price,  # Required by MEXC
        }
        
        try:
            logger.info(f"Placing TAKE_PROFIT_LIMIT order: {symbol} {side} {quantity} @ stop price {stop_price}, limit price {limit_price}")
            result = await self._make_request('POST', '/api/v3/order', params)
            logger.info(f"Take profit order placed: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to place take profit order: {str(e)}")
            
            # Try with a simpler approach - regular limit order at take profit price
            try:
                logger.info("Trying regular LIMIT order as take profit fallback...")
                params_fallback = {
                    'symbol': symbol,
                    'side': side,
                    'type': 'LIMIT',
                    'timeInForce': time_in_force,
                    'quantity': quantity,
                    'price': stop_price,
                }
                result = await self._make_request('POST', '/api/v3/order', params_fallback)
                logger.info(f"Fallback limit order placed as take profit: {result}")
                return result
            except Exception as e2:
                logger.error(f"Take profit fallback also failed: {str(e2)}")
                raise e

    async def place_sequential_bracket_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float, 
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        time_in_force: str = 'GTC'
    ) -> Dict[str, Any]:
        """
        Place a sequential bracket order:
        1. Place LIMIT order first
        2. Monitor until filled
        3. Place TAKE_PROFIT and STOP_LOSS orders
        """
        
        logger.info(f"Starting sequential bracket order for {symbol}:")
        logger.info(f"  Entry: {side} {quantity} @ {entry_price}")
        logger.info(f"  Stop Loss: {stop_loss_price}")
        logger.info(f"  Take Profit: {take_profit_price}")
        
        # Step 1: Place the main LIMIT order
        main_params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'timeInForce': time_in_force,
            'quantity': quantity,
            'price': entry_price
        }
        
        try:
            main_order = await self._make_request('POST', '/api/v3/order', main_params)
            logger.info(f"Main LIMIT order placed: {main_order}")
            
            # Return information for the trading engine to monitor
            return {
                'main_order': main_order,
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'entry_price': entry_price,
                'stop_loss_price': stop_loss_price,
                'take_profit_price': take_profit_price,
                'bracket_type': 'sequential',
                'requires_monitoring': True
            }
            
        except Exception as e:
            logger.error(f"Failed to place main LIMIT order: {str(e)}")
            raise e

    async def place_protective_orders_after_fill(
        self,
        symbol: str,
        original_side: str,
        quantity: float,
        stop_loss_price: float,
        take_profit_price: float
    ) -> Dict[str, Any]:
        """
        Place protective orders after main order is filled using MEXC's native order types
        
        Use MEXC's native conditional order types:
        - STOP_LOSS_LIMIT: Native exchange stop loss protection
        - TAKE_PROFIT_LIMIT: Native exchange take profit execution
        """
        
        # Determine the exit side (opposite of entry)
        exit_side = 'SELL' if original_side == 'BUY' else 'BUY'
        
        logger.info(f"Setting up MEXC native protection for filled {original_side} position:")
        logger.info(f"  Symbol: {symbol}")
        logger.info(f"  Quantity: {quantity}")
        logger.info(f"  Original Entry Side: {original_side}")
        logger.info(f"  Calculated Exit Side: {exit_side}")
        logger.info(f"  Stop Loss: {stop_loss_price} (MEXC STOP_LOSS_LIMIT)")
        logger.info(f"  Take Profit: {take_profit_price} (Regular LIMIT order)")
        
        # Validate logic: For BUY positions, exit should always be SELL
        if original_side == 'BUY' and exit_side != 'SELL':
            logger.error(f"❌ LOGIC ERROR: BUY position should have SELL exit, got {exit_side}")
            raise ValueError(f"Invalid exit side calculation: BUY position should exit with SELL orders")
        elif original_side == 'SELL' and exit_side != 'BUY':
            logger.error(f"❌ LOGIC ERROR: SELL position should have BUY exit, got {exit_side}")
            raise ValueError(f"Invalid exit side calculation: SELL position should exit with BUY orders")
        
        results = {
            'stop_loss_order': None,
            'take_profit_order': None,
            'software_stop_loss': False,  # We're using native orders now
            'stop_loss_price': stop_loss_price,
            'errors': []
        }
        
        # Store symbol for quantity formatting
        self._current_symbol = symbol
        
        # Get proper quantity formatting
        try:
            precision_info = await self.get_symbol_precision_info(symbol)
            step_size = precision_info.get('stepSize', '0.1')
            formatted_quantity = self.format_quantity(quantity, step_size)
        except Exception as e:
            logger.warning(f"Could not get precision info, using default formatting: {e}")
            formatted_quantity = round(quantity, 1)  # Conservative fallback for XRP
        
        # 1. Place MEXC native STOP_LOSS_LIMIT order
        try:
            # Try STOP_LOSS_LIMIT first (most preferred)
            # For STOP_LOSS_LIMIT orders, we need both stopPrice and price
            # Set limit price slightly worse than stop price to ensure execution
            if exit_side == 'SELL':
                # For sell stop loss, limit price should be slightly below stop price
                stop_limit_price = stop_loss_price * 0.999  # 0.1% below stop price
            else:
                # For buy stop loss, limit price should be slightly above stop price
                stop_limit_price = stop_loss_price * 1.001  # 0.1% above stop price
            
            stop_loss_params = {
                'symbol': symbol,
                'side': exit_side,
                'type': 'STOP_LOSS_LIMIT',
                'timeInForce': 'GTC',
                'quantity': formatted_quantity,
                'stopPrice': stop_loss_price,  # Trigger price
                'price': stop_limit_price      # Execution price
            }
            
            logger.info(f"Attempting MEXC STOP_LOSS_LIMIT order:")
            logger.info(f"  Side: {exit_side}")
            logger.info(f"  Quantity: {formatted_quantity}")
            logger.info(f"  Stop Price (trigger): {stop_loss_price}")
            logger.info(f"  Limit Price (execution): {stop_limit_price}")
            
            stop_loss_result = await self._make_request('POST', '/api/v3/order', stop_loss_params)
            results['stop_loss_order'] = stop_loss_result
            logger.info(f"✅ MEXC STOP_LOSS_LIMIT order placed: {stop_loss_result.get('orderId', 'Unknown')}")
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"STOP_LOSS_LIMIT failed: {error_msg}")
            
            # Try simpler STOP_LOSS order type (market order triggered at stop price)
            if "invalid type" in error_msg:
                try:
                    logger.info("Trying simpler MEXC STOP_LOSS order type...")
                    
                    stop_loss_params_simple = {
                        'symbol': symbol,
                        'side': exit_side,
                        'type': 'STOP_LOSS',
                        'quantity': formatted_quantity,
                        'stopPrice': stop_loss_price
                    }
                    
                    stop_loss_result = await self._make_request('POST', '/api/v3/order', stop_loss_params_simple)
                    results['stop_loss_order'] = stop_loss_result
                    logger.info(f"✅ MEXC STOP_LOSS order placed: {stop_loss_result.get('orderId', 'Unknown')}")
                    
                except Exception as simple_e:
                    logger.warning(f"Simple STOP_LOSS also failed: {simple_e}")
                    
                    # Final fallback to software monitoring
                    logger.warning("All MEXC stop loss order types failed, using software monitoring")
                    results['software_stop_loss'] = True
                    results['stop_loss_order'] = {
                        'type': 'SOFTWARE_MONITORING',
                        'stop_price': stop_loss_price,
                        'side': exit_side,
                        'quantity': formatted_quantity
                    }
            else:
                # For other errors, fall back to software monitoring
                logger.warning("Falling back to software stop loss monitoring")
                results['software_stop_loss'] = True
                results['stop_loss_order'] = {
                    'type': 'SOFTWARE_MONITORING',
                    'stop_price': stop_loss_price,
                    'side': exit_side,
                    'quantity': formatted_quantity
                }
            
            results['errors'].append(f"MEXC stop loss orders failed: {error_msg}")
        
        # 2. Place regular LIMIT order for take profit (simpler and more reliable)
        try:
            # Check current price to ensure take profit order makes sense
            current_price = None
            try:
                ticker = await self.get_ticker_price(symbol)
                current_price = float(ticker.get('price', 0))
            except Exception as e:
                logger.warning(f"Could not get current price for take profit validation: {e}")
            
            should_place_tp = True
            if current_price:
                # Only place take profit if it won't execute immediately
                if exit_side == 'SELL' and take_profit_price <= current_price:
                    should_place_tp = False
                    logger.warning(f"Take profit price {take_profit_price} would execute immediately (current: {current_price})")
                elif exit_side == 'BUY' and take_profit_price >= current_price:
                    should_place_tp = False
                    logger.warning(f"Take profit price {take_profit_price} would execute immediately (current: {current_price})")
            
            if should_place_tp:
                # Place regular LIMIT order for take profit
                take_profit_params = {
                    'symbol': symbol,
                    'side': exit_side,
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'quantity': formatted_quantity,
                    'price': take_profit_price
                }
                
                logger.info(f"Placing regular LIMIT order for take profit:")
                logger.info(f"  Side: {exit_side}")
                logger.info(f"  Quantity: {formatted_quantity}")
                logger.info(f"  Limit Price: {take_profit_price}")
                
                take_profit_result = await self._make_request('POST', '/api/v3/order', take_profit_params)
                results['take_profit_order'] = take_profit_result
                logger.info(f"✅ Take profit LIMIT order placed: {take_profit_result.get('orderId', 'Unknown')}")
            else:
                logger.warning("Take profit would execute immediately, using software monitoring instead")
                results['take_profit_order'] = {
                    'type': 'SOFTWARE_MONITORING',
                    'target_price': take_profit_price,
                    'side': exit_side,
                    'quantity': formatted_quantity
                }
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Regular LIMIT order for take profit failed: {error_msg}")
            
            # Fall back to software monitoring
            logger.warning("Falling back to software take profit monitoring")
            results['take_profit_order'] = {
                'type': 'SOFTWARE_MONITORING',
                'target_price': take_profit_price,
                'side': exit_side,
                'quantity': formatted_quantity
            }
            
            results['errors'].append(f"Take profit LIMIT order failed: {error_msg}")
        
        logger.info(" MEXC Native protection setup complete:")
        if results['stop_loss_order'] and 'orderId' in results['stop_loss_order']:
            logger.info(f"  Stop Loss: MEXC STOP_LOSS_LIMIT order @ {stop_loss_price}")
        else:
            logger.info(f"  Stop Loss: SOFTWARE monitoring @ {stop_loss_price}")
        
        if results['take_profit_order'] and 'orderId' in results['take_profit_order']:
            logger.info(f"  Take Profit: MEXC TAKE_PROFIT_LIMIT order @ {take_profit_price}")
        else:
            logger.info(f"  Take Profit: SOFTWARE monitoring @ {take_profit_price}")
        
        return results

    async def place_bracket_limit_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: float, 
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        time_in_force: str = 'GTC'
    ) -> Dict[str, Any]:
        """
        Place a single LIMIT order with built-in stop loss and take profit using MEXC's native capabilities
        
        This uses MEXC's OCO (One-Cancels-Other) functionality to create a bracket order:
        - Entry order: LIMIT order at entry_price
        - Stop Loss: Triggers if price moves against position
        - Take Profit: Triggers if price moves in favor of position
        
        Args:
            symbol: Trading pair (e.g., 'XRPUSDT')
            side: 'BUY' or 'SELL'
            quantity: Amount to trade
            entry_price: Price for initial LIMIT order
            stop_loss_price: Price to trigger stop loss
            take_profit_price: Price to trigger take profit
            time_in_force: 'GTC', 'IOC', or 'FOK'
            
        Returns:
            Dict containing order details and IDs
        """
        
        logger.info(f"Placing bracket LIMIT order for {symbol}:")
        logger.info(f"  Side: {side}")
        logger.info(f"  Entry Price: ${entry_price}")
        logger.info(f"  Stop Loss: ${stop_loss_price}")
        logger.info(f"  Take Profit: ${take_profit_price}")
        logger.info(f"  Quantity: {quantity}")
        
        try:
            # Method 1: Try MEXC's native OCO order with stop loss and take profit
            oco_params = {
                'symbol': symbol,
                'side': side,
                'quantity': str(quantity),
                'price': str(entry_price),  # Entry limit price
                'stopPrice': str(stop_loss_price),  # Stop loss trigger
                'stopLimitPrice': str(stop_loss_price * 0.995 if side == 'BUY' else stop_loss_price * 1.005),  # Stop loss execution price
                'stopLimitTimeInForce': 'GTC',
                'listClientOrderId': f"bracket_{int(time.time() * 1000)}"  # Unique ID
            }
            
            logger.info("Attempting OCO bracket order...")
            result = await self._make_request('POST', '/api/v3/order/oco', oco_params)
            
            # If successful, also place take profit order
            if result and 'listOrderId' in result:
                logger.info(f"✅ OCO order placed successfully: {result['listOrderId']}")
                
                # Place separate take profit order
                try:
                    tp_params = {
                        'symbol': symbol,
                        'side': 'SELL' if side == 'BUY' else 'BUY',  # Opposite side
                        'type': 'LIMIT',
                        'timeInForce': 'GTC',
                        'quantity': str(quantity),
                        'price': str(take_profit_price)
                    }
                    
                    tp_result = await self._make_request('POST', '/api/v3/order', tp_params)
                    logger.info(f"✅ Take profit order placed: {tp_result.get('orderId')}")
                    
                    return {
                        'bracket_type': 'native_oco',
                        'main_order': result,
                        'take_profit_order': tp_result,
                        'entry_price': entry_price,
                        'stop_loss_price': stop_loss_price,
                        'take_profit_price': take_profit_price,
                        'quantity': quantity
                    }
                    
                except Exception as tp_e:
                    logger.warning(f"Take profit order failed: {tp_e}")
                    return {
                        'bracket_type': 'oco_only',
                        'main_order': result,
                        'entry_price': entry_price,
                        'stop_loss_price': stop_loss_price,
                        'take_profit_price': take_profit_price,
                        'quantity': quantity,
                        'note': 'Take profit order failed, only stop loss active'
                    }
            
        except Exception as oco_e:
            logger.warning(f"OCO bracket order failed: {oco_e}")
            
            # Method 2: Try placing separate orders in sequence
            try:
                logger.info("Falling back to sequential order placement...")
                
                # 1. Place main entry order
                entry_params = {
                    'symbol': symbol,
                    'side': side,
                    'type': 'LIMIT',
                    'timeInForce': time_in_force,
                    'quantity': str(quantity),
                    'price': str(entry_price)
                }
                
                entry_result = await self._make_request('POST', '/api/v3/order', entry_params)
                logger.info(f"✅ Entry order placed: {entry_result.get('orderId')}")
                
                # 2. Place conditional stop loss order
                try:
                    sl_params = {
                        'symbol': symbol,
                        'side': 'SELL' if side == 'BUY' else 'BUY',  # Opposite side
                        'type': 'STOP_LOSS_LIMIT',
                        'timeInForce': 'GTC',
                        'quantity': str(quantity),
                        'stopPrice': str(stop_loss_price),
                        'price': str(stop_loss_price * 0.99 if side == 'BUY' else stop_loss_price * 1.01)  # Execution price
                    }
                    
                    sl_result = await self._make_request('POST', '/api/v3/order', sl_params)
                    logger.info(f"✅ Stop loss order placed: {sl_result.get('orderId')}")
                    
                except Exception as sl_e:
                    logger.warning(f"Stop loss order failed: {sl_e}")
                    sl_result = None
                
                # 3. Place conditional take profit order
                try:
                    tp_params = {
                        'symbol': symbol,
                        'side': 'SELL' if side == 'BUY' else 'BUY',  # Opposite side
                        'type': 'TAKE_PROFIT_LIMIT',
                        'timeInForce': 'GTC',
                        'quantity': str(quantity),
                        'stopPrice': str(take_profit_price),
                        'price': str(take_profit_price)
                    }
                    
                    tp_result = await self._make_request('POST', '/api/v3/order', tp_params)
                    logger.info(f"✅ Take profit order placed: {tp_result.get('orderId')}")
                    
                except Exception as tp_e:
                    logger.warning(f"Take profit order failed: {tp_e}")
                    tp_result = None
                
                return {
                    'bracket_type': 'sequential_native',
                    'main_order': entry_result,
                    'stop_loss_order': sl_result,
                    'take_profit_order': tp_result,
                    'entry_price': entry_price,
                    'stop_loss_price': stop_loss_price,
                    'take_profit_price': take_profit_price,
                    'quantity': quantity,
                    'note': 'Orders placed separately using MEXC native order types'
                }
                
            except Exception as sequential_e:
                logger.error(f"Sequential native orders also failed: {sequential_e}")
                raise sequential_e
    
    async def get_symbol_precision_info(self, symbol: str) -> Dict[str, Any]:
        """Get quantity and price precision information for a symbol"""
        try:
            exchange_info = await self.get_exchange_info(symbol)
            if not exchange_info.get('symbols'):
                return {"error": "Symbol not found"}
            
            symbol_info = exchange_info['symbols'][0]
            
            # Default precision values
            precision_info = {
                "symbol": symbol,
                "baseAssetPrecision": 8,  # Default for most crypto
                "quoteAssetPrecision": 8,
                "quotePrecision": 8,
                "baseCommissionPrecision": 8,
                "quoteCommissionPrecision": 8,
                "stepSize": "0.00000001",  # Default step size
                "tickSize": "0.00000001",
                "minQty": "0.00000001",
                "maxQty": "9000000000.00000000"
            }
            
            # Update with actual values from exchange info
            precision_info.update({
                "baseAssetPrecision": symbol_info.get('baseAssetPrecision', 8),
                "quoteAssetPrecision": symbol_info.get('quoteAssetPrecision', 8),
                "quotePrecision": symbol_info.get('quotePrecision', 8),
                "baseCommissionPrecision": symbol_info.get('baseCommissionPrecision', 8),
                "quoteCommissionPrecision": symbol_info.get('quoteCommissionPrecision', 8),
            })
            
            # Extract LOT_SIZE filter for step size and min/max quantity
            for filter_info in symbol_info.get('filters', []):
                filter_type = filter_info.get('filterType', '')
                if filter_type == 'LOT_SIZE':
                    precision_info.update({
                        "stepSize": filter_info.get('stepSize', precision_info['stepSize']),
                        "minQty": filter_info.get('minQty', precision_info['minQty']),
                        "maxQty": filter_info.get('maxQty', precision_info['maxQty'])
                    })
                elif filter_type == 'PRICE_FILTER':
                    precision_info.update({
                        "tickSize": filter_info.get('tickSize', precision_info['tickSize']),
                        "minPrice": filter_info.get('minPrice', '0.00000001'),
                        "maxPrice": filter_info.get('maxPrice', '1000000.00000000')
                    })
            
            logger.debug(f"Precision info for {symbol}: {precision_info}")
            return precision_info
            
        except Exception as e:
            logger.error(f"Failed to get precision info for {symbol}: {e}")
            return {"error": str(e)}
    
    def format_quantity(self, quantity: float, step_size: str) -> float:
        """Format quantity according to step size to avoid 'quantity scale is invalid' errors"""
        try:
            # Parse step size to determine decimal places
            step_decimal_places = len(step_size.rstrip('0').split('.')[1]) if '.' in step_size else 0
            step_float = float(step_size)
            
            # MEXC API sometimes returns unrealistic step sizes - implement fallbacks for major pairs
            symbol = getattr(self, '_current_symbol', 'UNKNOWN')
            
            # Common sense fallbacks for major trading pairs
            if 'USDT' in symbol and step_float < 0.001:
                logger.warning(f"Unrealistic step size {step_size} for {symbol}, using practical fallback")
                if 'BTC' in symbol or 'ETH' in symbol:
                    # High-value coins: use 0.001 (3 decimal places)
                    step_float = 0.001
                    step_decimal_places = 3
                elif any(coin in symbol for coin in ['XRP', 'ADA', 'DOGE', 'SHIB']):
                    # Lower-value coins but not micro: use 0.1 (1 decimal place) 
                    step_float = 0.1
                    step_decimal_places = 1
                else:
                    # Default for other USDT pairs: use 0.01 (2 decimal places)
                    step_float = 0.01
                    step_decimal_places = 2
                
                logger.info(f"Using practical step size: {step_float} ({step_decimal_places} decimal places)")
            
            # Round to appropriate decimal places
            formatted_quantity = round(quantity, step_decimal_places)
            
            # Ensure quantity is a multiple of step size
            if step_float > 0:
                # Round to nearest step size multiple
                formatted_quantity = round(formatted_quantity / step_float) * step_float
                # Round again to remove floating point precision errors
                formatted_quantity = round(formatted_quantity, step_decimal_places)
            
            logger.debug(f"Formatted quantity {quantity} to {formatted_quantity} using step size {step_float}")
            return formatted_quantity
            
        except Exception as e:
            logger.warning(f"Failed to format quantity {quantity} with step size {step_size}: {e}")
            # Emergency fallback based on symbol type
            symbol = getattr(self, '_current_symbol', 'UNKNOWN')
            if 'XRP' in symbol:
                # XRP is typically traded in whole numbers or 0.1 increments
                return round(quantity, 1)
            elif any(coin in symbol for coin in ['BTC', 'ETH']):
                # High-value coins need more precision
                return round(quantity, 3)
            else:
                # Conservative default
                return round(quantity, 2) 
