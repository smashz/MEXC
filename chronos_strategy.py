import numpy as np
import pandas as pd
from config import PREDICTION_LENGTH, CONFIDENCE_THRESHOLD, MAX_HISTORICAL_DATA
import json
import os
from datetime import datetime

class ChronosTradingStrategy:
    def __init__(self):
        """
        Advanced statistical forecasting strategy with learning capability
        """
        print("Using advanced statistical forecasting engine")
        self.prediction_length = PREDICTION_LENGTH
        self.confidence_threshold = CONFIDENCE_THRESHOLD
        self.historical_data = []
        self.learning_rate = 0.1
        self.weights = {
            'ma_alignment': 0.4,
            'momentum': 0.3,
            'rsi': 0.2,
            'roc': 0.1
        }
        self.model_path = 'chronos_model.json'
        self._load_model()
        
    def _load_model(self):
        """Load learned weights from file"""
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, 'r') as f:
                    saved_data = json.load(f)
                    self.weights = saved_data.get('weights', self.weights)
                print("AI model weights loaded from file")
            except:
                print("Failed to load model, using default weights")
    
    def _save_model(self):
        """Save learned weights to file"""
        try:
            data_to_save = {
                'weights': self.weights,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.model_path, 'w') as f:
                json.dump(data_to_save, f, indent=2)
        except Exception as e:
            print(f"Error saving model: {e}")
    
    def update_data(self, new_price: float):
        """
        Update historical data with new price point
        """
        self.historical_data.append(new_price)
        # Keep only the most recent data
        if len(self.historical_data) > MAX_HISTORICAL_DATA:
            self.historical_data = self.historical_data[-MAX_HISTORICAL_DATA:]
    
    def calculate_technical_indicators(self, prices):
        """
        Calculate various technical indicators
        """
        prices = np.array(prices)
        indicators = {}
        
        # Moving averages
        indicators['sma_5'] = np.mean(prices[-5:]) if len(prices) >= 5 else prices[-1]
        indicators['sma_10'] = np.mean(prices[-10:]) if len(prices) >= 10 else prices[-1]
        indicators['sma_20'] = np.mean(prices[-20:]) if len(prices) >= 20 else prices[-1]
        indicators['sma_50'] = np.mean(prices[-50:]) if len(prices) >= 50 else prices[-1]
        
        # Moving average alignment score
        ma_scores = []
        if len(prices) >= 5: ma_scores.append(1 if indicators['sma_5'] > indicators['sma_10'] else -1)
        if len(prices) >= 10: ma_scores.append(1 if indicators['sma_10'] > indicators['sma_20'] else -1)
        if len(prices) >= 20: ma_scores.append(1 if indicators['sma_20'] > indicators['sma_50'] else -1)
        indicators['ma_alignment'] = np.mean(ma_scores) if ma_scores else 0
        
        # Momentum
        if len(prices) >= 2:
            indicators['momentum'] = (prices[-1] - prices[-2]) / prices[-2] * 100  # Convert to percentage
        else:
            indicators['momentum'] = 0
            
        # Rate of Change
        if len(prices) >= 10:
            indicators['roc'] = (prices[-1] - prices[-10]) / prices[-10] * 100  # Convert to percentage
        else:
            indicators['roc'] = 0
            
        # Volatility
        if len(prices) >= 10:
            indicators['volatility'] = np.std(prices[-10:]) / prices[-1] * 100  # Percentage volatility
        else:
            indicators['volatility'] = 0
            
        # RSI approximation
        if len(prices) >= 14:
            gains = np.where(np.diff(prices[-15:]) > 0, np.diff(prices[-15:]), 0)
            losses = np.where(np.diff(prices[-15:]) < 0, -np.diff(prices[-15:]), 0)
            avg_gain = np.mean(gains) if len(gains) > 0 else 0.01
            avg_loss = np.mean(losses) if len(losses) > 0 else 0.01
            
            if avg_loss == 0:
                indicators['rsi'] = 100
            else:
                rs = avg_gain / avg_loss
                indicators['rsi'] = 100 - (100 / (1 + rs))
        else:
            indicators['rsi'] = 50
            
        # Normalize RSI to -1 to 1 range for better learning
        indicators['rsi_normalized'] = (indicators['rsi'] - 50) / 50
            
        return indicators
    
    def predict(self) -> dict:
        """
        Generate price predictions using advanced statistical methods
        """
        if len(self.historical_data) < 10:
            return {
                "predictions": [], 
                "confidence": 0, 
                "trend": "neutral",
                "price_change_pct": 0,
                "current_price": 0,
                "predicted_price": 0,
                "trend_score": 0
            }
        
        current_price = self.historical_data[-1]
        indicators = self.calculate_technical_indicators(self.historical_data)
        
        # Calculate trend score using learned weights
        trend_score = (
            self.weights['ma_alignment'] * indicators['ma_alignment'] +
            self.weights['momentum'] * (indicators['momentum'] / 100) +  # Normalize momentum
            self.weights['rsi'] * indicators['rsi_normalized'] +
            self.weights['roc'] * (indicators['roc'] / 100)  # Normalize ROC
        )
        
        # Apply sigmoid function to get bounded prediction
        trend_score = 2 / (1 + np.exp(-trend_score * 3)) - 1  # Scaled sigmoid
        
        # Determine trend and generate predictions
        volatility_factor = max(0.001, min(0.02, indicators['volatility'] / 100))  # 0.1% to 2%
        
        if trend_score > 0.15:
            # Bullish trend
            trend = "bullish"
            predictions = [current_price * (1 + volatility_factor * i * trend_score) 
                          for i in range(1, self.prediction_length + 1)]
            price_change_pct = volatility_factor * self.prediction_length * trend_score * 100
            
        elif trend_score < -0.15:
            # Bearish trend
            trend = "bearish"
            predictions = [current_price * (1 + volatility_factor * i * trend_score) 
                          for i in range(1, self.prediction_length + 1)]
            price_change_pct = volatility_factor * self.prediction_length * trend_score * 100
            
        else:
            # Neutral trend
            trend = "neutral"
            predictions = [current_price] * self.prediction_length
            price_change_pct = 0
        
        confidence = min(0.95, abs(trend_score))
        
        return {
            "predictions": predictions,
            "confidence": confidence,
            "trend": trend,
            "price_change_pct": price_change_pct,
            "current_price": current_price,
            "predicted_price": np.mean(predictions[-3:]),  # Average of last 3 predictions
            "trend_score": trend_score
        }
    
    def generate_signal(self) -> str:
        """
        Generate trading signal based on predictions
        """
        prediction_result = self.predict()
        
        if prediction_result["confidence"] < self.confidence_threshold:
            return "HOLD"
        
        if prediction_result["trend"] == "bullish" and prediction_result["trend_score"] > 0.2:
            return "BUY"
        elif prediction_result["trend"] == "bearish" and prediction_result["trend_score"] < -0.2:
            return "SELL"
        else:
            return "HOLD"
    
    def get_prediction_info(self) -> dict:
        """
        Get detailed prediction information for display
        """
        return self.predict()
    
    def learn_from_trade(self, features, actual_outcome, profit):
        """
        Reinforcement learning - adjust weights based on trade results
        """
        if not features:
            return
            
        # Calculate what our prediction would have been
        predicted_score = (
            self.weights['ma_alignment'] * features.get('ma_alignment', 0) +
            self.weights['momentum'] * (features.get('momentum', 0) / 100) +
            self.weights['rsi'] * features.get('rsi_normalized', 0) +
            self.weights['roc'] * (features.get('roc', 0) / 100)
        )
        
        # Calculate error
        error = actual_outcome - predicted_score
        
        # Update weights using gradient descent
        learning_rate = self.learning_rate * (1 + abs(profit) / 1000)  # Adjust learning based on profit magnitude
        
        self.weights['ma_alignment'] += learning_rate * error * features.get('ma_alignment', 0)
        self.weights['momentum'] += learning_rate * error * (features.get('momentum', 0) / 100)
        self.weights['rsi'] += learning_rate * error * features.get('rsi_normalized', 0)
        self.weights['roc'] += learning_rate * error * (features.get('roc', 0) / 100)
        
        # Normalize weights to prevent explosion
        total = sum(abs(w) for w in self.weights.values())
        if total > 2.0:
            scale_factor = 2.0 / total
            for key in self.weights:
                self.weights[key] *= scale_factor
        
        self._save_model()
        print(f"AI learned: error={error:.3f}, new weights: {self.weights}")
    
    def extract_features(self, prices):
        """Extract features for learning"""
        if len(prices) < 10:
            return None
        return self.calculate_technical_indicators(prices)