from typing import Dict, Any, Optional, List
import aiohttp
import hmac
import hashlib
import time
from loguru import logger
from config import MexcCredentials

class MexcClient:
    """High-performance async MEXC API client with rate limiting and error handling"""
    
    BASE_URL = "https://api.mexc.com"
    
    def __init__(self, credentials: MexcCredentials, rate_limit_rps: float = 10.0):
        self.api_key = credentials.api_key
        self.secret_key = credentials.secret_key
        self._session = None
        self._rate_limit = rate_limit_rps
        self._last_request_time = 0
        
    @property
    def session(self) -> aiohttp.ClientSession:
        """Lazy initialization of aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
        
    async def __aenter__(self):
        """Async context manager entry"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self._session and not self._session.closed:
            await self._session.close()
            
    def _generate_signature(self, query_string: str) -> str:
        """Generate HMAC SHA256 signature for API requests"""
        return hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
    async def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, signed: bool = False) -> Dict:
        """Make an API request with rate limiting and error handling"""
        await self._respect_rate_limit()
        
        url = f"{self.BASE_URL}{endpoint}"
        headers = {"X-MEXC-APIKEY": self.api_key} if signed else {}
        
        try:
            async with self.session.request(method, url, params=params, headers=headers) as response:
                if response.status == 429:
                    logger.warning("Rate limit hit, backing off...")
                    await asyncio.sleep(1)
                    return await self._make_request(method, endpoint, params, signed)
                    
                data = await response.json()
                
                if response.status != 200:
                    error_msg = f"API Error {response.status}: {data}"
                    logger.error(error_msg)
                    raise Exception(f"MEXC API Error {response.status}: {data}")
                    
                return data
                
        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            raise
            
    async def _respect_rate_limit(self):
        """Ensure we don't exceed the API rate limit"""
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        
        if time_since_last < (1 / self._rate_limit):
            await asyncio.sleep((1 / self._rate_limit) - time_since_last)
            
        self._last_request_time = current_time
        
    async def get_exchange_info(self) -> Dict[str, Any]:
        """Get exchange trading rules and symbol information"""
        return await self._make_request("GET", "/api/v3/exchangeInfo")
        
    async def get_account(self) -> Dict[str, Any]:
        """Get account information"""
        return await self._make_request("GET", "/api/v3/account", signed=True)
        
    async def get_klines(self, symbol: str, interval: str = "5m", limit: int = 1000) -> List[List[float]]:
        """Get candlestick data for analysis"""
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        }
        
        try:
            data = await self._make_request("GET", "/api/v3/klines", params=params)
            return [[float(x) for x in kline] for kline in data]
        except Exception as e:
            logger.error(f"Failed to get klines: {e}")
            return []
            
    async def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Get latest price for a symbol"""
        try:
            data = await self._make_request("GET", "/api/v3/ticker/price", {"symbol": symbol.upper()})
            return float(data["price"]) if "price" in data else None
        except Exception as e:
            logger.error(f"Failed to get ticker price: {e}")
            return None
            
    async def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None, test: bool = False) -> Optional[Dict[str, Any]]:
        """Place a new order"""
        endpoint = "/api/v3/order/test" if test else "/api/v3/order"
        
        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
        }
        
        if price and order_type.upper() != "MARKET":
            params["price"] = str(price)
            
        try:
            return await self._make_request("POST", endpoint, params=params, signed=True)
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None