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
        if not credentials.api_key or not credentials.secret_key:
            raise ValueError("API key and secret key are required. Please check your .env file and ensure MEXC_API_KEY and MEXC_SECRET_KEY are set correctly.")
        
        self.api_key = credentials.api_key
        self.secret_key = credentials.secret_key
        self.passphrase = credentials.passphrase
        self.rate_limit_rps = rate_limit_rps
        self.session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0
        self._request_count = 0
        self._rate_limit_lock = asyncio.Lock()
        
        logger.info("MEXC API client initialized successfully")
        
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        )
        # Validate credentials by making a test API call
        await self.validate_credentials()
        return self

    async def validate_credentials(self):
        """Validate API credentials by making a test API call"""
        if not self.api_key or not self.secret_key:
            raise ValueError("API key and secret key are required but missing. Please check your .env file.")
            
        try:
            # Test API access by getting account info
            info = await self.get_account()
            if info is not None:
                logger.info("API credentials validated successfully")
                return True
            else:
                raise ValueError("Failed to validate API credentials")
        except Exception as e:
            logger.error(f"Failed to validate API credentials: {str(e)}")
            raise
            
    async def get_account(self) -> Dict[str, Any]:
        """Get account information including balances"""
        if not self.session:
            raise RuntimeError("API client session not initialized")
            
        endpoint = "/api/v3/account"
        timestamp = int(time.time() * 1000)
        params = {'timestamp': timestamp}
        signature = self._generate_signature(urllib.parse.urlencode(params))
        params['signature'] = signature
        
        headers = {
            'X-MEXC-APIKEY': self.api_key,
        }
        
        try:
            async with self.session.get(
                f"{self.BASE_URL}{endpoint}",
                params=params,
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.debug(f"Account info received: {data}")
                    return data
                else:
                    logger.error(f"Failed to get account info: {response.status}")
                    error_text = await response.text()
                    logger.error(f"Error response: {error_text}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching account info: {str(e)}")
            return None
            
        try:
            # Try to get account information
            await self.get_account_info()
            logger.info("API credentials validated successfully")
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                raise ValueError(f"Invalid API credentials: Authorization failed. Please check your MEXC_API_KEY and MEXC_SECRET_KEY in the .env file.")
            else:
                raise ValueError(f"API error (HTTP {e.status}): {str(e)}")
        except Exception as e:
            logger.error(f"Failed to validate API credentials: {str(e)}")
            raise ValueError(f"API validation failed: {str(e)}. Please check your MEXC_API_KEY and MEXC_SECRET_KEY in the .env file.")
    
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

    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information to validate API credentials"""
        endpoint = "/api/v3/account"
        timestamp = int(time.time() * 1000)
        query_string = f"timestamp={timestamp}"
        signature = self._generate_signature(query_string)
        
        headers = {
            "X-MEXC-APIKEY": self.api_key,
        }
        
        url = f"{self.BASE_URL}{endpoint}?{query_string}&signature={signature}"
        
        async with self.session.get(url, headers=headers) as response:
            if response.status != 200:
                text = await response.text()
                raise ValueError(f"API request failed: {text}")
            return await response.json()

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