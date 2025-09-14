import hmac
import time
import json
import asyncio
import aiohttp
import hashlib
import urllib.parse
from typing import Dict, Any, Optional, List
import aiohttp
import hmac
import hashlib
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

    async def get_ticker_price(self, symbol: str) -> Dict[str, Any]:
        """Get current ticker price for a symbol"""
        endpoint = "/api/v3/ticker/price"
        params = {
            "symbol": symbol.upper()
        }
        
        if not self.session:
            logger.error("API client session not initialized")
            await self.__aenter__()
        
        try:
            logger.debug(f"Fetching ticker price for {symbol}")
            url = f"{self.BASE_URL}{endpoint}"
            logger.debug(f"Full URL: {url}")
            logger.debug(f"Request params: {params}")
            
            async with self.session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                response_text = await response.text()
                logger.debug(f"Response status: {response.status}")
                logger.debug(f"Raw response: {response_text[:200]}...")
                
                if response.status != 200:
                    logger.error(f"API error getting ticker price: {response.status} - {response_text}")
                    return {"price": "0"}
                
                try:
                    ticker = json.loads(response_text)
                    logger.debug(f"Parsed ticker response: {ticker}")
                    return ticker
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse ticker price JSON response: {e}")
                    return {"price": "0"}
                
        except asyncio.TimeoutError:
            logger.error("Timeout while fetching ticker price")
            return {"price": "0"}
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching ticker price: {e}")
            return {"price": "0"}
        except Exception as e:
            logger.error(f"Error fetching ticker price: {str(e)}")
            return {"price": "0"}
        
    async def get_klines(self, symbol: str, interval: str = "5m", limit: int = 1000) -> List[List[float]]:
        """Get candlestick data for analysis"""
        endpoint = "/api/v3/klines"
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        }
        
        if not self.session:
            logger.error("API client session not initialized")
            await self.__aenter__()
        
        try:
            logger.debug(f"Fetching klines for {symbol} [{interval}] with limit {limit}")
            url = f"{self.BASE_URL}{endpoint}"
            logger.debug(f"Full URL: {url}")
            logger.debug(f"Request params: {params}")
            
            async with self.session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                response_text = await response.text()
                logger.debug(f"Response status: {response.status}")
                logger.debug(f"Raw response: {response_text[:200]}...")  # Log first 200 chars
                
                if response.status != 200:
                    logger.error(f"API error: {response.status} - {response_text}")
                    return []
                
                try:
                    klines = json.loads(response_text)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON response: {e}")
                    return []
                
                if not isinstance(klines, list):
                    logger.error(f"Invalid klines response format: {type(klines)}")
                    return []
                
                processed_klines = []
                for kline in klines:
                    try:
                        processed_kline = [float(x) for x in kline]
                        processed_klines.append(processed_kline)
                    except (ValueError, TypeError, IndexError) as e:
                        logger.error(f"Error processing kline data: {e}")
                        continue
                
                if not processed_klines:
                    logger.warning("No valid klines data received")
                else:
                    logger.info(f"Successfully fetched {len(processed_klines)} klines")
                
                return processed_klines
                
        except asyncio.TimeoutError:
            logger.error("Timeout while fetching klines")
            return []
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching klines: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching klines: {str(e)}")
            return []
    
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
    
    async def get_account_balance(self) -> Dict[str, Dict[str, float]]:
        """Get account balances"""
        endpoint = "/api/v3/account"
        
        try:
            response = await self._make_request('GET', endpoint, {}, signed=True)
            balances = {}
            
            if 'balances' in response:
                for balance in response['balances']:
                    asset = balance['asset']
                    free = float(balance['free'])
                    locked = float(balance['locked'])
                    
                    if free > 0 or locked > 0:
                        balances[asset] = {
                            'free': free,
                            'locked': locked,
                            'total': free + locked
                        }
            
            logger.debug(f"Retrieved balances for {len(balances)} assets")
            return balances
            
        except Exception as e:
            logger.error(f"Error fetching account balances: {e}")
            return {}

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
            try:
                # Fall back to backup endpoint if primary fails
                result = await self._make_request('GET', '/api/v3/exchangeInformation', params, signed=False)
                logger.info("Successfully retrieved exchange info from backup endpoint")
                return result
            except Exception as e:
                logger.error(f"Both primary and backup exchange info endpoints failed: {e}")
                return {}