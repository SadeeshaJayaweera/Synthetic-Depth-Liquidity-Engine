"""Core liquidity metrics module for market microstructure analysis.

Computes high-frequency liquidity proxies and synthetic depth metrics:
1. Kyle's Lambda (Price impact regression parameter: delta_P / signed_order_flow)
2. Amihud Illiquidity Ratio (|return| / dollar_volume)
3. Quote-Implied Depth & Replenishment Speed (Post-trade NBBO size recovery)
4. Effective Spread & Realized Spread (Microstructure spread decomposition)
5. VPIN (Volume-Synchronized Probability of Informed Trading)
6. LiquidityScore (0-100 normalized composite liquidity health indicator)
"""

from typing import Any, Dict, List, Optional, Tuple, Union
import logging
import numpy as np
import pandas as pd
from scipy.stats import norm

from synthetic_depth.microstructure.classification import TradeDirection, classify_bulk_volume_bvc
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

logger = logging.getLogger(__name__)

# Default metric weights for LiquidityScore
DEFAULT_METRIC_WEIGHTS = {
    "kyle_lambda": 0.25,
    "amihud_ratio": 0.20,
    "replenishment_speed": 0.20,
    "effective_spread": 0.20,
    "vpin": 0.15,
}


def compute_kyle_lambda(
    classified_trades_df: pd.DataFrame,
    time_bucket: str = "1min",
    window: int = 10,
) -> pd.Series:
    """Compute Kyle's Lambda (Price Impact Coefficient).

    Formula:
        Delta P_t = alpha + lambda * (V_buy,t - V_sell,t) + epsilon_t
        lambda = Cov(Delta P, OFI) / Var(OFI)

    Microstructure Intuition:
        Lambda measures the price impact per unit of signed order flow ($ / share).
        A steeper slope (higher lambda) indicates a thin, shallow order book where
        trades exert large permanent price dislocations.

    Args:
        classified_trades_df: DataFrame with 'timestamp', 'price', 'size', 'direction'.
        time_bucket: Resampling interval for aggregation (default: '1min').
        window: Rolling window of bars for regression calculation.

    Returns:
        Pandas Series of estimated Kyle's Lambda values indexed by timestamp.
    """
    if classified_trades_df is None or classified_trades_df.empty:
        return pd.Series(dtype=float)

    df = classified_trades_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype("datetime64[ns]")
    df = df.set_index("timestamp").sort_index()

    # Signed volume: +size for BUY, -size for SELL, 0 for UNKNOWN
    df["signed_vol"] = np.where(
        df["direction"] == TradeDirection.BUY.value,
        df["size"],
        np.where(df["direction"] == TradeDirection.SELL.value, -df["size"], 0),
    )

    bars = df["price"].resample(time_bucket).ohlc()
    bars["ofi"] = df["signed_vol"].resample(time_bucket).sum().fillna(0.0)
    bars = bars.dropna(subset=["close"])

    bars["delta_p"] = bars["close"].diff().fillna(0.0)

    # Rolling covariance and variance for OLS slope: cov(X, Y) / var(X)
    rolling_cov = bars["delta_p"].rolling(window=window, min_periods=3).cov(bars["ofi"])
    rolling_var = bars["ofi"].rolling(window=window, min_periods=3).var()

    kyle_lambda = rolling_cov / rolling_var.replace(0.0, np.nan)
    # Kyle's lambda is theoretically positive; bound extreme negative noise at 0
    kyle_lambda = kyle_lambda.clip(lower=0.0).fillna(0.0)

    return kyle_lambda


def compute_amihud_ratio(
    classified_trades_df: pd.DataFrame,
    time_bucket: str = "1min",
) -> pd.Series:
    """Compute the Amihud (2002) Illiquidity Ratio.

    Formula:
        ILLIQ_t = |Return_t| / Dollar_Volume_t
        Return_t = |P_close,t - P_open,t| / P_open,t
        Dollar_Volume_t = Sum(P_i * Size_i)

    Microstructure Intuition:
        Measures the absolute price response per dollar of trading volume.
        High Amihud ratio = low liquidity (prices move easily on low dollar volume).

    Args:
        classified_trades_df: DataFrame with 'timestamp', 'price', 'size'.
        time_bucket: Resampling interval for aggregation.

    Returns:
        Pandas Series of Amihud ratio values (scaled by 1e6 for readability).
    """
    if classified_trades_df is None or classified_trades_df.empty:
        return pd.Series(dtype=float)

    df = classified_trades_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype("datetime64[ns]")
    df = df.set_index("timestamp").sort_index()

    df["dollar_vol"] = df["price"] * df["size"]

    bars = df["price"].resample(time_bucket).ohlc()
    bars["dollar_volume"] = df["dollar_vol"].resample(time_bucket).sum().fillna(0.0)
    bars = bars.dropna(subset=["close"])

    open_p = bars["open"].replace(0.0, np.nan)
    abs_return = (bars["close"] - bars["open"]).abs() / open_p

    amihud = (abs_return / bars["dollar_volume"].replace(0.0, np.nan)).fillna(0.0)
    # Standard scaling factor for intraday Amihud (x 1e6)
    return amihud * 1_000_000.0


def compute_replenishment_speed(
    classified_trades_df: pd.DataFrame,
    quotes_df: pd.DataFrame,
    size_percentile: float = 75.0,
    window_sec: float = 5.0,
) -> float:
    """Compute Quote-Implied Depth Proxy & Replenishment Speed around large prints.

    Logic:
        1. Identify 'large' trade prints (size >= size_percentile).
        2. For each large trade at t_trade:
           - Measure baseline pre-trade top-of-book depth D_pre = mean(bid_size + ask_size)
             over [t_trade - window_sec, t_trade).
           - Inspect post-trade quotes over [t_trade, t_trade + window_sec].
           - Measure time delta (in milliseconds) until post-trade depth recovers to >= 90% of D_pre.
        3. Compute median recovery time across all large prints.

    Microstructure Intuition:
        In deep, resilient books, market makers and algorithmic liquidity providers
        instantly replenish depleted quotes (< 50-150ms). In shallow books, depth
        remains depleted for seconds after large prints.

    Args:
        classified_trades_df: DataFrame with 'timestamp', 'size'.
        quotes_df: DataFrame with 'timestamp', 'bid_size', 'ask_size'.
        size_percentile: Percentile threshold defining a large trade print.
        window_sec: Lookback and lookahead window in seconds.

    Returns:
        Median replenishment speed in milliseconds (lower = faster/deeper).
    """
    if (
        classified_trades_df is None
        or classified_trades_df.empty
        or quotes_df is None
        or quotes_df.empty
    ):
        return 1000.0  # Default neutral recovery speed in ms

    trades = classified_trades_df.copy()
    trades["timestamp"] = pd.to_datetime(trades["timestamp"]).astype("datetime64[ns]")
    trades = trades.sort_values("timestamp").reset_index(drop=True)

    quotes = quotes_df.copy()
    quotes["timestamp"] = pd.to_datetime(quotes["timestamp"]).astype("datetime64[ns]")
    quotes = quotes.sort_values("timestamp").reset_index(drop=True)
    quotes["depth"] = quotes["bid_size"] + quotes["ask_size"]

    threshold_size = trades["size"].quantile(size_percentile / 100.0)
    large_trades = trades[trades["size"] >= threshold_size]

    if large_trades.empty:
        return 100.0

    recovery_times_ms: List[float] = []

    for _, tr in large_trades.iterrows():
        t_tr = tr["timestamp"]
        t_pre = t_tr - pd.Timedelta(seconds=window_sec)
        t_post = t_tr + pd.Timedelta(seconds=window_sec)

        # Pre-trade window depth
        pre_mask = (quotes["timestamp"] >= t_pre) & (quotes["timestamp"] < t_tr)
        if not np.any(pre_mask):
            continue
        baseline_depth = quotes.loc[pre_mask, "depth"].mean()
        if baseline_depth <= 0:
            continue

        target_depth = 0.90 * baseline_depth

        # Post-trade window depth
        post_mask = (quotes["timestamp"] >= t_tr) & (quotes["timestamp"] <= t_post)
        post_quotes = quotes.loc[post_mask]
        if post_quotes.empty:
            continue

        recovered = post_quotes[post_quotes["depth"] >= target_depth]
        if not recovered.empty:
            rec_time = (recovered.iloc[0]["timestamp"] - t_tr).total_seconds() * 1000.0
            recovery_times_ms.append(max(0.0, rec_time))
        else:
            # Did not recover within window_sec
            recovery_times_ms.append(window_sec * 1000.0)

    if not recovery_times_ms:
        return 500.0

    return float(np.median(recovery_times_ms))


def compute_spreads(
    classified_trades_df: pd.DataFrame,
    quotes_df: pd.DataFrame,
    delta_sec: float = 300.0,
) -> pd.DataFrame:
    """Compute Effective Spread and Realized Spread.

    Formulas:
        Effective Spread:
            ES_k = 2 * D_k * (P_k - M_k)
        Realized Spread (Adverse Selection component):
            RS_k = 2 * D_k * (P_k - M_{k + delta})
        where D_k is +1 for BUY, -1 for SELL, and M is the quote midpoint.

    Microstructure Intuition:
        Effective spread represents the gross round-trip transaction cost paid by market orders.
        Realized spread measures the net revenue earned by liquidity providers after adverse
        selection (price moving against the provider over delta_sec).

    Args:
        classified_trades_df: DataFrame with 'timestamp', 'price', 'direction', 'mid_price'.
        quotes_df: DataFrame with 'timestamp', 'bid_price', 'ask_price'.
        delta_sec: Lookahead interval in seconds for realized spread (default: 300s / 5min).

    Returns:
        DataFrame with columns 'effective_spread' and 'realized_spread' for each trade.
    """
    if classified_trades_df is None or classified_trades_df.empty:
        return pd.DataFrame(columns=["effective_spread", "realized_spread"])

    trades = classified_trades_df.copy()
    trades["timestamp"] = pd.to_datetime(trades["timestamp"]).astype("datetime64[ns]")
    trades = trades.sort_values("timestamp").reset_index(drop=True)

    # Direction sign
    d_sign = np.where(
        trades["direction"] == TradeDirection.BUY.value,
        1.0,
        np.where(trades["direction"] == TradeDirection.SELL.value, -1.0, 0.0),
    )

    # Effective spread
    mid = trades["mid_price"].to_numpy()
    valid_mid = ~np.isnan(mid) & (mid > 0)
    effective_spread = np.where(
        valid_mid & (d_sign != 0),
        2.0 * d_sign * (trades["price"].to_numpy() - mid),
        2.0 * np.abs(trades["price"].to_numpy() - np.where(valid_mid, mid, trades["price"].to_numpy())),
    )

    # Realized spread: match future midpoint at t + delta_sec
    if quotes_df is not None and not quotes_df.empty:
        quotes = quotes_df.copy()
        quotes["timestamp"] = pd.to_datetime(quotes["timestamp"]).astype("datetime64[ns]")
        quotes["future_mid"] = (quotes["bid_price"] + quotes["ask_price"]) / 2.0
        quotes = quotes.sort_values("timestamp").reset_index(drop=True)

        trades["future_time"] = (trades["timestamp"] + pd.to_timedelta(delta_sec, unit="s")).astype("datetime64[ns]")
        merged_future = pd.merge_asof(
            trades,
            quotes[["timestamp", "future_mid"]],
            left_on="future_time",
            right_on="timestamp",
            direction="backward",
        )
        f_mid = merged_future["future_mid"].to_numpy()
        realized_spread = np.where(
            ~np.isnan(f_mid) & (d_sign != 0),
            2.0 * d_sign * (trades["price"].to_numpy() - f_mid),
            effective_spread,  # fallback if future quote unavailable
        )
    else:
        realized_spread = effective_spread.copy()

    trades["effective_spread"] = effective_spread
    trades["realized_spread"] = realized_spread
    return trades[["effective_spread", "realized_spread"]]


def compute_vpin(
    trades_df: pd.DataFrame,
    num_buckets: int = 50,
    window: int = 50,
) -> pd.DataFrame:
    """Compute VPIN (Volume-Synchronized Probability of Informed Trading).

    Formula:
        Volume Bucket Size V = Total Volume / num_buckets
        Order Imbalance I_tau = |V_tau^BUY - V_tau^SELL|
        VPIN = Sum_{j=0}^{N-1} I_{tau-j} / (N * V)

    Microstructure Intuition:
        VPIN measures order flow toxicity and the presence of informed traders
        in continuous, volume-synchronized clock time. Spikes in VPIN frequently
        signal impending volatility and liquidity flight.

    Args:
        trades_df: DataFrame with 'timestamp', 'price', 'size'.
        num_buckets: Total number of volume buckets to partition volume into.
        window: Number of volume buckets in rolling VPIN estimate (default: 50).

    Returns:
        DataFrame with columns 'bucket_id', 'timestamp', 'buy_vol', 'sell_vol', 'imbalance', 'vpin'.
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(columns=["bucket_id", "timestamp", "buy_vol", "sell_vol", "imbalance", "vpin"])

    df = trades_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype("datetime64[ns]")
    df = df.sort_values("timestamp").reset_index(drop=True)

    total_volume = df["size"].sum()
    if total_volume <= 0:
        return pd.DataFrame(columns=["bucket_id", "timestamp", "buy_vol", "sell_vol", "imbalance", "vpin"])

    bucket_size = max(100.0, float(total_volume) / float(max(1, num_buckets)))

    # Estimate buy probability per trade using price drift (BVC proxy on ticks)
    df["price_diff"] = df["price"].diff().fillna(0.0)
    std = df["price_diff"].std()
    sigma = std if not np.isnan(std) and std > 0 else 1.0
    df["buy_prob"] = norm.cdf(df["price_diff"] / sigma)

    buckets: List[Dict[str, Any]] = []
    curr_buy = 0.0
    curr_sell = 0.0
    curr_vol = 0.0
    bucket_ts = df.iloc[0]["timestamp"]

    for _, row in df.iterrows():
        rem_size = float(row["size"])
        prob = float(row["buy_prob"])
        ts = row["timestamp"]

        while rem_size > 0:
            space = bucket_size - curr_vol
            fill = min(space, rem_size)

            curr_buy += fill * prob
            curr_sell += fill * (1.0 - prob)
            curr_vol += fill
            rem_size -= fill
            bucket_ts = ts

            if curr_vol >= bucket_size:
                imb = abs(curr_buy - curr_sell)
                buckets.append({
                    "bucket_id": len(buckets) + 1,
                    "timestamp": bucket_ts,
                    "buy_vol": curr_buy,
                    "sell_vol": curr_sell,
                    "imbalance": imb,
                })
                curr_buy = 0.0
                curr_sell = 0.0
                curr_vol = 0.0

    if curr_vol > 0:
        buckets.append({
            "bucket_id": len(buckets) + 1,
            "timestamp": bucket_ts,
            "buy_vol": curr_buy,
            "sell_vol": curr_sell,
            "imbalance": abs(curr_buy - curr_sell),
        })

    bucket_df = pd.DataFrame(buckets)
    if bucket_df.empty:
        return pd.DataFrame(columns=["bucket_id", "timestamp", "buy_vol", "sell_vol", "imbalance", "vpin"])

    # Rolling VPIN calculation
    rolling_n = min(window, len(bucket_df))
    rolling_imb = bucket_df["imbalance"].rolling(window=rolling_n, min_periods=1).sum()
    bucket_df["vpin"] = (rolling_imb / (rolling_n * bucket_size)).clip(0.0, 1.0)

    return bucket_df


def compute_liquidity_score(
    metrics_df: pd.DataFrame,
    weights: Optional[Dict[str, float]] = None,
) -> pd.Series:
    """Aggregate individual liquidity proxies into a normalized 0-100 LiquidityScore.

    Methodology:
        1. Invert each metric such that higher values represent better liquidity:
           - Lower Kyle's Lambda -> deeper book (+score)
           - Lower Amihud Ratio -> lower price impact (+score)
           - Lower Replenishment Speed (faster ms) -> faster recovery (+score)
           - Tighter Effective Spread -> lower cost (+score)
           - Lower VPIN -> less toxic flow (+score)
        2. Standardize each inverted feature Z_i = (X_i - mu) / sigma.
        3. Compute weighted composite linear combination: C = sum(w_i * Z_i).
        4. Pass through logistic sigmoid to bound smoothly within [0, 100]:
           Score = 100 / (1 + exp(-C))

    Args:
        metrics_df: DataFrame containing metric columns:
            ['kyle_lambda', 'amihud_ratio', 'replenishment_speed_ms', 'effective_spread', 'vpin']
        weights: Optional dictionary mapping metric names to relative importance weights.

    Returns:
        Pandas Series of liquidity scores bounded in [0, 100].
    """
    if metrics_df is None or metrics_df.empty:
        return pd.Series(dtype=float)

    w = weights or DEFAULT_METRIC_WEIGHTS

    components = {}
    metric_keys = {
        "kyle_lambda": "kyle_lambda",
        "amihud_ratio": "amihud_ratio",
        "replenishment_speed": "replenishment_speed_ms",
        "effective_spread": "effective_spread",
        "vpin": "vpin",
    }

    for name, col in metric_keys.items():
        if col in metrics_df.columns:
            vals = metrics_df[col].to_numpy(dtype=float)
            # Invert: lower raw value = higher liquidity
            inv_vals = -vals
            std = np.nanstd(inv_vals)
            mu = np.nanmean(inv_vals)
            if std > 0:
                z = (inv_vals - mu) / std
            else:
                z = np.zeros_like(inv_vals)
            components[name] = np.nan_to_num(z, nan=0.0)
        else:
            components[name] = np.zeros(len(metrics_df))

    # Weighted composite sum
    composite = np.zeros(len(metrics_df))
    total_w = sum(w.get(k, 0.0) for k in components)
    if total_w <= 0:
        total_w = 1.0

    for name, z_arr in components.items():
        weight = w.get(name, 0.2)
        composite += (weight / total_w) * z_arr

    # Sigmoid mapping to [0, 100]
    scores = 100.0 / (1.0 + np.exp(-composite))
    return pd.Series(scores, index=metrics_df.index).round(2)


class LiquidityMetricsCalculator:
    """Orchestrates end-to-end liquidity metrics extraction, scoring, and persistence."""

    def __init__(self, storage: Optional[DuckDBStorage] = None):
        """Initialize LiquidityMetricsCalculator.

        Args:
            storage: DuckDBStorage instance.
        """
        self.storage = storage or DuckDBStorage()

    def calculate_symbol_date(
        self,
        symbol: str,
        date_str: str,
        time_bucket: str = "1min",
        persist: bool = True,
        weights: Optional[Dict[str, float]] = None,
    ) -> pd.DataFrame:
        """Calculate high-frequency liquidity metrics for a symbol on a specific date.

        Args:
            symbol: Stock ticker symbol (e.g. 'AAPL')
            date_str: Trading date ('YYYY-MM-DD')
            time_bucket: Resampling bucket size (e.g. '1min', '5min')
            persist: Whether to store results in DuckDB liquidity_metrics table
            weights: Optional custom weight overrides for LiquidityScore

        Returns:
            DataFrame of aggregated liquidity metrics.
        """
        symbol = symbol.upper().strip()
        start_time = f"{date_str} 00:00:00"
        end_time = f"{date_str} 23:59:59.999999"

        logger.info(f"Loading classified trades and quotes for {symbol} on {date_str}...")
        classified_df = self.storage.get_classified_trades(symbol, start_time=start_time, end_time=end_time)
        quotes_df = self.storage.get_quotes(symbol, start_time=start_time, end_time=end_time)

        if classified_df.empty:
            logger.warning(f"No classified trades found for {symbol} on {date_str}. Run classification first.")
            return pd.DataFrame()

        logger.info(f"Calculating microstructure liquidity metrics (bucket={time_bucket})...")

        # 1. Kyle's Lambda per bucket
        kyle_series = compute_kyle_lambda(classified_df, time_bucket=time_bucket)

        # 2. Amihud Ratio per bucket
        amihud_series = compute_amihud_ratio(classified_df, time_bucket=time_bucket)

        # 3. Replenishment speed (symbol/day scalar propagated across buckets)
        replenish_ms = compute_replenishment_speed(classified_df, quotes_df)

        # 4. Spreads (Effective & Realized) resampled per bucket
        spreads_df = compute_spreads(classified_df, quotes_df)
        classified_with_spreads = classified_df.copy()
        classified_with_spreads["timestamp"] = pd.to_datetime(classified_with_spreads["timestamp"]).astype("datetime64[ns]")
        classified_with_spreads["effective_spread"] = spreads_df["effective_spread"]
        classified_with_spreads["realized_spread"] = spreads_df["realized_spread"]

        spreads_resampled = (
            classified_with_spreads.set_index("timestamp")[["effective_spread", "realized_spread"]]
            .resample(time_bucket)
            .mean()
        )

        # 5. VPIN calculation resampled to time buckets
        vpin_df = compute_vpin(classified_df, num_buckets=50, window=50)
        if not vpin_df.empty:
            vpin_df["timestamp"] = pd.to_datetime(vpin_df["timestamp"]).astype("datetime64[ns]")
            vpin_resampled = vpin_df.set_index("timestamp")["vpin"].resample(time_bucket).last().ffill().bfill()
        else:
            vpin_resampled = pd.Series(0.0, index=kyle_series.index)

        # Combine all metrics on common time index
        metrics_df = pd.DataFrame(index=kyle_series.index)
        metrics_df["symbol"] = symbol
        metrics_df["kyle_lambda"] = kyle_series
        metrics_df["amihud_ratio"] = amihud_series
        metrics_df["replenishment_speed_ms"] = replenish_ms
        metrics_df["effective_spread"] = spreads_resampled["effective_spread"].ffill().bfill().fillna(0.01)
        metrics_df["realized_spread"] = spreads_resampled["realized_spread"].ffill().bfill().fillna(0.01)
        metrics_df["vpin"] = vpin_resampled.reindex(metrics_df.index).ffill().bfill().fillna(0.20)

        # 6. Composite LiquidityScore
        metrics_df["liquidity_score"] = compute_liquidity_score(metrics_df, weights=weights)

        metrics_df = metrics_df.reset_index()

        if persist and not metrics_df.empty:
            inserted = self.storage.insert_liquidity_metrics_dataframe(metrics_df)
            logger.info(f"Persisted {inserted} liquidity metrics rows for {symbol} on {date_str}")

        return metrics_df
