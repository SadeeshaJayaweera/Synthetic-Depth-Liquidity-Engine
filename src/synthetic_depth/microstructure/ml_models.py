import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
import logging
import pickle
import os

logger = logging.getLogger(__name__)

class LiquidityPredictor:
    """Advanced Machine Learning Model to predict short-term liquidity changes."""
    
    def __init__(self, model_path: str = "liquidity_rf_model.pkl"):
        self.model_path = model_path
        self.model = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42)
        self.scaler = StandardScaler()
        self.is_trained = False
        self._load_model()
        
    def _load_model(self):
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, 'rb') as f:
                    saved = pickle.load(f)
                    self.model = saved['model']
                    self.scaler = saved['scaler']
                    self.is_trained = True
                    logger.info("Loaded pre-trained LiquidityPredictor model.")
            except Exception as e:
                logger.error(f"Failed to load model: {e}")
                
    def save_model(self):
        if self.is_trained:
            try:
                with open(self.model_path, 'wb') as f:
                    pickle.dump({'model': self.model, 'scaler': self.scaler}, f)
                    logger.info("Saved LiquidityPredictor model.")
            except Exception as e:
                logger.error(f"Failed to save model: {e}")

    def train(self, historical_features: pd.DataFrame, target_spreads: pd.Series):
        """Train the model on historical microstructure features."""
        logger.info("Training LiquidityPredictor...")
        X_scaled = self.scaler.fit_transform(historical_features)
        self.model.fit(X_scaled, target_spreads)
        self.is_trained = True
        self.save_model()
        logger.info("Training complete.")

    def predict_next_spread(self, current_features: dict) -> float:
        """Predict the effective spread for the next time window.
        
        Expected features: 'volume', 'volatility', 'kyle_lambda', 'vpin', 'order_imbalance'
        """
        if not self.is_trained:
            logger.warning("Model is not trained. Returning naive estimate.")
            return current_features.get('spread', 0.05)
            
        try:
            df = pd.DataFrame([current_features])
            expected_cols = ['volume', 'volatility', 'kyle_lambda', 'vpin', 'order_imbalance']
            for col in expected_cols:
                if col not in df.columns:
                    df[col] = 0.0 # fallback
                    
            X_scaled = self.scaler.transform(df[expected_cols])
            pred = self.model.predict(X_scaled)[0]
            return max(0.01, float(pred))
        except Exception as e:
            logger.error(f"Prediction failed: {e}")
            return current_features.get('spread', 0.05)
