from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np
from loguru import logger
from config import load_config, BotConfig
import logging_config

class ChronosTradingStrategy:
    """Advanced trading strategy using trend analysis"""
    
    def __init__(self, config: Optional[BotConfig] = None):
        self.config = config or load_config()
        self.historical_prices: List[float] = []
        self.last_signal = "NEUTRAL"
        self.last_confidence = 0.0
        self.last_prediction_time = None
        self._last_update = None
        self._price_predictions = []
        
        # Initialize strategy parameters from config
        ai_config = self.config.ai_config
        self.prediction_length = ai_config.prediction_length
        self.lookback_periods = ai_config.lookback_periods
        self.confidence_threshold = ai_config.confidence_threshold
        self.min_trend_strength = ai_config.min_trend_strength
        self.max_historical_data = ai_config.max_historical_data
        self.timeframe = ai_config.timeframe
        self.feature_columns = ai_config.feature_columns
        self.target_column = ai_config.target_column
        
    def update_data(self, price: float) -> None:
        """Update strategy with new price data"""
        try:
            if price is not None and price > 0:
                self.historical_prices.append(price)
                
                # Keep only the most recent data points
                if len(self.historical_prices) > self.max_historical_data:
                    self.historical_prices = self.historical_prices[-self.max_historical_data:]
                
                # Update last update time
                self._last_update = datetime.now()
        except Exception as e:
            logger.error(f"Error updating price data: {e}")
    
    def predict(self) -> Dict[str, Any]:
        """Generate predictions using trend analysis"""
        try:
            with logging_config.ai_context():
                if len(self.historical_prices) < self.lookback_periods:
                    logger.warning(f"Insufficient data: {len(self.historical_prices)}/{self.lookback_periods}")
                    return {
                        'direction': 'NEUTRAL',
                        'confidence': 0.0,
                        'prediction': None,
                        'error': 'Insufficient data'
                    }
            
            # Calculate trend
            recent_prices = self.historical_prices[-self.lookback_periods:]
            price_changes = np.diff(recent_prices)
            trend_strength = np.abs(np.mean(price_changes))
            
            if trend_strength < self.min_trend_strength:
                return {
                    'direction': 'NEUTRAL',
                    'confidence': 0.0,
                    'prediction': None,
                    'trend_strength': trend_strength
                }
            
            # Determine direction
            trend_direction = 'UP' if np.mean(price_changes) > 0 else 'DOWN'
            confidence = min(trend_strength / self.min_trend_strength, 1.0)
            
            # Calculate prediction
            current_price = self.historical_prices[-1]
            predicted_change = np.mean(price_changes) * self.prediction_length
            predicted_price = current_price + predicted_change
            
            self.last_signal = trend_direction
            self.last_confidence = confidence
            self.last_prediction_time = datetime.now()
            
            return {
                'direction': trend_direction,
                'confidence': confidence,
                'prediction': predicted_price,
                'trend_strength': trend_strength,
                'timestamp': self.last_prediction_time.isoformat()
            }
        except Exception as e:
            error_info = {
                'direction': 'NEUTRAL',
                'confidence': 0.0,
                'prediction': None,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
            logger.error(f"AI Prediction error: {e}\nFull details: {error_info}")
            return error_info
    
    def get_last_prediction(self) -> Dict[str, Any]:
        """Get the last prediction"""
        try:
            if self.last_prediction_time is None or \
               (datetime.now() - self.last_prediction_time).total_seconds() > self.config.ai_config.update_interval:
                return self.predict()
            
            return {
                'direction': self.last_signal,
                'confidence': self.last_confidence,
                'prediction': None,
                'timestamp': self.last_prediction_time.isoformat()
            }
        except Exception as e:
            logger.error(f"Error getting prediction: {e}")
            return {
                'direction': 'NEUTRAL',
                'confidence': 0.0,
                'prediction': None,
                'error': str(e)
            }
