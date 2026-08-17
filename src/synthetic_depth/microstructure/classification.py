"""Trade classification module implementing the Lee-Ready algorithm and Bulk Volume Classification (BVC).

Infers buyer-initiated vs. seller-initiated trade flow from tick data and prevailing quotes.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
import logging
import numpy as np
import pandas as pd
from scipy.stats import norm

from synthetic_depth.storage.duckdb_layer import DuckDBStorage

logger = logging.getLogger(__name__)


class TradeDirection(str, Enum):
    """Classified trade initiation direction."""

    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class ClassificationMethod(str, Enum):
    """Method used to classify trade direction."""

    QUOTE_RULE = "QUOTE_RULE"
    TICK_RULE = "TICK_RULE"
    BVC = "BVC"
    UNKNOWN = "UNKNOWN"


def classify_trades_lee_ready(
    trades_df: pd.DataFrame,
    quotes_df: pd.DataFrame,
    lag_ms: float = 0.0,
) -> pd.DataFrame:
    """Classify trade ticks as BUY or SELL using the Lee-Ready (1991) algorithm.

    Algorithm Logic:
    1. Match each trade with the latest prevailing NBBO quote at or prior to
       (trade_timestamp - lag_ms).
    2. Quote Rule:
       - If Trade Price > Midpoint: BUY (QUOTE_RULE)
       - If Trade Price < Midpoint: SELL (QUOTE_RULE)
    3. Tick Rule (for Trade Price == Midpoint or when Quote is unavailable):
       - If Trade Price > Prev Price: BUY (TICK_RULE - Uptick)
       - If Trade Price < Prev Price: SELL (TICK_RULE - Downtick)
       - If Trade Price == Prev Price: Inherit previous tick sign (Zero-Uptick/Downtick)
       - If First Trade with no quote and no prior price: UNKNOWN

    Args:
        trades_df: DataFrame of trade ticks (columns: symbol, timestamp, price, size, ...)
        quotes_df: DataFrame of NBBO quote ticks (columns: symbol, timestamp, bid_price, ask_price, ...)
        lag_ms: Quote reporting delay in milliseconds (default 0.0).

    Returns:
        DataFrame of classified trades with bid_price, ask_price, mid_price,
        direction, and classification_method.
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(columns=[
            "symbol", "timestamp", "price", "size", "exchange", "conditions",
            "trade_id", "bid_price", "ask_price", "mid_price", "direction",
            "classification_method",
        ])

    trades = trades_df.copy()
    trades["timestamp"] = pd.to_datetime(trades["timestamp"]).astype("datetime64[ns]")
    trades = trades.sort_values("timestamp").reset_index(drop=True)

    if quotes_df is not None and not quotes_df.empty:
        quotes = quotes_df.copy()
        quotes["timestamp"] = pd.to_datetime(quotes["timestamp"]).astype("datetime64[ns]")
        quotes = quotes.sort_values("timestamp").reset_index(drop=True)

        # Apply reporting lag: quote at t_quote must be <= t_trade - lag_ms
        # Equivalently, shift quote match timestamp by +lag_ms
        if lag_ms > 0:
            quotes["match_time"] = (quotes["timestamp"] + pd.to_timedelta(lag_ms, unit="ms")).astype("datetime64[ns]")
            trades["match_time"] = trades["timestamp"]
            merged = pd.merge_asof(
                trades,
                quotes[["match_time", "bid_price", "ask_price"]],
                on="match_time",
                direction="backward",
            )
            merged.drop(columns=["match_time"], inplace=True)
        else:
            merged = pd.merge_asof(
                trades,
                quotes[["timestamp", "bid_price", "ask_price"]],
                on="timestamp",
                direction="backward",
            )
    else:
        merged = trades.copy()
        merged["bid_price"] = np.nan
        merged["ask_price"] = np.nan

    # Calculate midpoint where quotes are valid (bid > 0 and ask >= bid)
    valid_quotes = (
        merged["bid_price"].notna()
        & merged["ask_price"].notna()
        & (merged["bid_price"] > 0)
        & (merged["ask_price"] >= merged["bid_price"])
    )
    merged["mid_price"] = np.where(
        valid_quotes,
        (merged["bid_price"] + merged["ask_price"]) / 2.0,
        np.nan,
    )

    prices = merged["price"].to_numpy()
    mid_prices = merged["mid_price"].to_numpy()
    n = len(prices)

    directions: List[str] = [TradeDirection.UNKNOWN.value] * n
    methods: List[str] = [ClassificationMethod.UNKNOWN.value] * n

    prev_price: Optional[float] = None
    last_tick_direction: Optional[str] = None

    for i in range(n):
        p = prices[i]
        m = mid_prices[i]

        classified = False

        # 1. Quote Rule
        if not np.isnan(m):
            if p > m:
                directions[i] = TradeDirection.BUY.value
                methods[i] = ClassificationMethod.QUOTE_RULE.value
                classified = True
            elif p < m:
                directions[i] = TradeDirection.SELL.value
                methods[i] = ClassificationMethod.QUOTE_RULE.value
                classified = True

        # 2. Tick Rule (at midpoint or no quote available)
        if not classified:
            if prev_price is not None:
                if p > prev_price:
                    directions[i] = TradeDirection.BUY.value
                    methods[i] = ClassificationMethod.TICK_RULE.value
                    last_tick_direction = TradeDirection.BUY.value
                elif p < prev_price:
                    directions[i] = TradeDirection.SELL.value
                    methods[i] = ClassificationMethod.TICK_RULE.value
                    last_tick_direction = TradeDirection.SELL.value
                else:  # p == prev_price (zero-tick)
                    if last_tick_direction is not None:
                        directions[i] = last_tick_direction
                        methods[i] = ClassificationMethod.TICK_RULE.value
                    else:
                        directions[i] = TradeDirection.UNKNOWN.value
                        methods[i] = ClassificationMethod.UNKNOWN.value
            else:
                # First trade with price at midpoint or no quote
                directions[i] = TradeDirection.UNKNOWN.value
                methods[i] = ClassificationMethod.UNKNOWN.value

        # Update previous price and tick history
        if prev_price is None or p != prev_price:
            if prev_price is not None:
                if p > prev_price:
                    last_tick_direction = TradeDirection.BUY.value
                elif p < prev_price:
                    last_tick_direction = TradeDirection.SELL.value
            prev_price = p

    merged["direction"] = directions
    merged["classification_method"] = methods

    # Ensure required columns
    expected_cols = [
        "symbol", "timestamp", "price", "size", "exchange", "conditions",
        "trade_id", "bid_price", "ask_price", "mid_price", "direction",
        "classification_method",
    ]
    for col in expected_cols:
        if col not in merged.columns:
            merged[col] = None

    return merged[expected_cols]


def classify_bulk_volume_bvc(
    trades_df: pd.DataFrame,
    time_bucket: str = "1min",
    window: int = 20,
) -> pd.DataFrame:
    """Classify volume probabilistically within time buckets using Bulk Volume Classification (BVC).

    BVC partitions total volume in a bar into buy and sell volumes:
        Z_tau = delta_P_tau / sigma_delta_P
        V_tau_BUY = V_tau * Phi(Z_tau)
        V_tau_SELL = V_tau * (1 - Phi(Z_tau))

    Args:
        trades_df: DataFrame of trade ticks (columns: timestamp, price, size)
        time_bucket: Frequency for resampling (e.g., '1min', '5min')
        window: Rolling window length for standard deviation estimation

    Returns:
        DataFrame with OHLCV bars and estimated buy_volume, sell_volume, buy_ratio.
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "delta_price", "sigma", "z_score", "buy_volume", "sell_volume", "buy_ratio",
        ])

    df = trades_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype("datetime64[ns]")
    df = df.set_index("timestamp").sort_index()

    bars = df["price"].resample(time_bucket).ohlc()
    bars["volume"] = df["size"].resample(time_bucket).sum().fillna(0)
    bars = bars.dropna(subset=["close"])

    bars["delta_price"] = bars["close"].diff().fillna(0.0)

    # Rolling standard deviation of price changes
    rolling_std = bars["delta_price"].rolling(window=window, min_periods=2).std()
    global_std = bars["delta_price"].std()
    fallback_std = global_std if not np.isnan(global_std) and global_std > 0 else 1.0
    bars["sigma"] = rolling_std.fillna(fallback_std).replace(0.0, fallback_std)

    # Standardized price change Z
    bars["z_score"] = bars["delta_price"] / bars["sigma"]

    # CDF mapping: Phi(Z)
    bars["buy_ratio"] = norm.cdf(bars["z_score"])
    bars["buy_volume"] = bars["volume"] * bars["buy_ratio"]
    bars["sell_volume"] = bars["volume"] * (1.0 - bars["buy_ratio"])

    return bars.reset_index()


class TradeClassifier:
    """Orchestrator for classifying trades stored in DuckDB and persisting results."""

    def __init__(self, storage: Optional[DuckDBStorage] = None):
        """Initialize TradeClassifier.

        Args:
            storage: DuckDBStorage instance. If None, default storage is loaded.
        """
        self.storage = storage or DuckDBStorage()

    def classify_symbol_date(
        self,
        symbol: str,
        date_str: str,
        lag_ms: float = 0.0,
        persist: bool = True,
    ) -> pd.DataFrame:
        """Fetch trades and quotes for a symbol on a given date, classify, and optionally persist.

        Args:
            symbol: Stock ticker symbol (e.g. 'AAPL')
            date_str: Date string ('YYYY-MM-DD')
            lag_ms: Quote reporting lag in milliseconds
            persist: Whether to save results to DuckDB classified_trades table

        Returns:
            Classified trades DataFrame.
        """
        symbol = symbol.upper().strip()
        start_time = f"{date_str} 00:00:00"
        end_time = f"{date_str} 23:59:59.999999"

        logger.info(f"Loading trades and quotes for {symbol} on {date_str}...")
        trades_df = self.storage.get_trades(symbol, start_time=start_time, end_time=end_time)
        quotes_df = self.storage.get_quotes(symbol, start_time=start_time, end_time=end_time)

        if trades_df.empty:
            logger.warning(f"No raw trades found in DuckDB for {symbol} on {date_str}")
            return pd.DataFrame()

        logger.info(f"Classifying {len(trades_df)} trades against {len(quotes_df)} quotes (lag={lag_ms}ms)...")
        classified = classify_trades_lee_ready(trades_df, quotes_df, lag_ms=lag_ms)

        if persist and not classified.empty:
            inserted = self.storage.insert_classified_trades_dataframe(classified)
            logger.info(f"Persisted {inserted} classified trades for {symbol} on {date_str}")

        return classified

    def compute_bvc_bars(
        self,
        symbol: str,
        date_str: str,
        time_bucket: str = "1min",
        window: int = 20,
    ) -> pd.DataFrame:
        """Compute Bulk Volume Classification (BVC) bars for a given symbol and date."""
        symbol = symbol.upper().strip()
        start_time = f"{date_str} 00:00:00"
        end_time = f"{date_str} 23:59:59.999999"

        trades_df = self.storage.get_trades(symbol, start_time=start_time, end_time=end_time)
        if trades_df.empty:
            return pd.DataFrame()

        return classify_bulk_volume_bvc(trades_df, time_bucket=time_bucket, window=window)
