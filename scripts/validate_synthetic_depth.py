"""Validation script comparing Synthetic Depth slippage estimates against realized market price impacts.

Computes empirical statistical correlation (Pearson r, Spearman rho), MAE, and RMSE
between the model's ex-ante estimated slippage and the ex-post realized price impacts
observed in historical tick data.
"""

from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

from synthetic_depth.microstructure.synthetic_book import (
    SyntheticDepthEstimator,
    SlippageEstimator,
)
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


def run_validation_for_symbol(
    storage: DuckDBStorage,
    symbol: str,
    date_str: str,
    delta_sec: float = 300.0,
    sample_size: int = 500,
) -> Dict[str, Any]:
    """Run proxy validation for a symbol by comparing predicted slippage vs realized drift.

    Args:
        storage: DuckDBStorage instance.
        symbol: Ticker symbol.
        date_str: Trading date ('YYYY-MM-DD').
        delta_sec: Realized forward window in seconds (default: 300s / 5min).
        sample_size: Maximum trade sample size to evaluate.

    Returns:
        Dictionary of correlation and error metrics.
    """
    symbol = symbol.upper().strip()
    start_time = f"{date_str} 00:00:00"
    end_time = f"{date_str} 23:59:59.999999"

    trades_df = storage.get_classified_trades(symbol, start_time=start_time, end_time=end_time)
    quotes_df = storage.get_quotes(symbol, start_time=start_time, end_time=end_time)
    metrics_df = storage.get_liquidity_metrics(symbol, start_time=start_time, end_time=end_time)

    if trades_df.empty or quotes_df.empty:
        return {"error": f"Insufficient data in DuckDB for {symbol} on {date_str}"}

    trades = trades_df.copy()
    trades["timestamp"] = pd.to_datetime(trades["timestamp"]).astype("datetime64[ns]")
    quotes = quotes_df.copy()
    quotes["timestamp"] = pd.to_datetime(quotes["timestamp"]).astype("datetime64[ns]")
    quotes["future_mid"] = (quotes["bid_price"] + quotes["ask_price"]) / 2.0

    # Match future quote at t + delta_sec
    trades["future_time"] = (trades["timestamp"] + pd.to_timedelta(delta_sec, unit="s")).astype("datetime64[ns]")
    
    quotes_for_merge = quotes[["timestamp", "future_mid"]].rename(columns={"timestamp": "quote_timestamp"}).sort_values("quote_timestamp")

    merged = pd.merge_asof(
        trades.sort_values("future_time"),
        quotes_for_merge,
        left_on="future_time",
        right_on="quote_timestamp",
        direction="backward",
    )

    merged = merged.dropna(subset=["mid_price", "future_mid"]).reset_index(drop=True)
    if merged.empty:
        return {"error": "No matched forward quotes found."}

    # Filter non-zero trades
    valid_trades = merged[merged["size"] > 0].copy()
    if len(valid_trades) > sample_size:
        valid_trades = valid_trades.sample(n=sample_size, random_state=42).sort_values("timestamp")

    kyle_lambda = 0.00005
    replenish_ms = 500.0
    if not metrics_df.empty:
        if pd.notna(metrics_df["kyle_lambda"].mean()) and metrics_df["kyle_lambda"].mean() > 0:
            kyle_lambda = float(metrics_df["kyle_lambda"].mean())
        if pd.notna(metrics_df["replenishment_speed_ms"].iloc[0]):
            replenish_ms = float(metrics_df["replenishment_speed_ms"].iloc[0])

    depth_estimator = SyntheticDepthEstimator(storage=storage)
    slippage_estimator = SlippageEstimator()

    model_slippage_bps_list: List[float] = []
    realized_impact_bps_list: List[float] = []
    size_list: List[int] = []

    for _, tr in valid_trades.iterrows():
        t_trade = tr["timestamp"]
        size = int(tr["size"])
        side = "BUY" if tr["direction"] == "BUY" else "SELL"
        mid_0 = float(tr["mid_price"])
        future_mid = float(tr["future_mid"])

        # Construct synthetic book at trade
        bid_p = float(tr.get("bid_price", mid_0 - 0.01))
        ask_p = float(tr.get("ask_price", mid_0 + 0.01))
        book = depth_estimator.build_synthetic_book(
            symbol=symbol,
            bid_price=bid_p,
            ask_price=ask_p,
            bid_size=200,
            ask_size=200,
            kyle_lambda=kyle_lambda,
            replenishment_speed_ms=replenish_ms,
            num_levels=15,
        )

        estimate = slippage_estimator.estimate_slippage(book, order_size=size, side=side)
        model_slippage_bps_list.append(estimate.slippage_bps)

        # Realized impact in bps: |future_mid - mid_0| / mid_0 * 10,000
        realized_impact_bps = (abs(future_mid - mid_0) / mid_0) * 10_000.0
        realized_impact_bps_list.append(realized_impact_bps)
        size_list.append(size)

    y_pred = np.array(model_slippage_bps_list)
    y_true = np.array(realized_impact_bps_list)

    pearson_corr, _ = pearsonr(y_pred, y_true) if len(y_pred) > 2 and np.std(y_pred) > 0 and np.std(y_true) > 0 else (0.0, 1.0)
    spearman_corr, _ = spearmanr(y_pred, y_true) if len(y_pred) > 2 and np.std(y_pred) > 0 and np.std(y_true) > 0 else (0.0, 1.0)

    mae = float(np.mean(np.abs(y_pred - y_true)))
    rmse = float(np.sqrt(np.mean((y_pred - y_true) ** 2)))

    # Breakdown by trade size bucket
    buckets = [(50, 100), (101, 300), (301, 500), (501, 10000)]
    bucket_breakdown = []
    sizes_arr = np.array(size_list)

    for low, high in buckets:
        mask = (sizes_arr >= low) & (sizes_arr <= high)
        if np.any(mask):
            b_pred = y_pred[mask]
            b_true = y_true[mask]
            b_mae = float(np.mean(np.abs(b_pred - b_true)))
            bucket_breakdown.append({
                "size_range": f"{low}-{high} shs",
                "count": int(np.sum(mask)),
                "mean_model_bps": round(float(np.mean(b_pred)), 2),
                "mean_realized_bps": round(float(np.mean(b_true)), 2),
                "mae_bps": round(b_mae, 2),
            })

    return {
        "symbol": symbol,
        "date": date_str,
        "sample_trades": len(y_pred),
        "pearson_r": round(float(pearson_corr), 4),
        "spearman_rho": round(float(spearman_corr), 4),
        "mae_bps": round(mae, 2),
        "rmse_bps": round(rmse, 2),
        "mean_model_slippage_bps": round(float(np.mean(y_pred)), 2),
        "mean_realized_impact_bps": round(float(np.mean(y_true)), 2),
        "bucket_breakdown": bucket_breakdown,
    }


def print_symbol_results(res: Dict[str, Any], title: str) -> None:
    """Print formatted validation summary for a symbol."""
    print(f"\n--- {title} ---")
    if "error" in res:
        print(f"Error: {res['error']}")
        return

    print(f"Sample Size:              {res['sample_trades']} trade executions")
    print(f"Pearson Correlation (r):  {res['pearson_r']}")
    print(f"Spearman Rank Corr (rho): {res['spearman_rho']}")
    print(f"Mean Absolute Error:      {res['mae_bps']} bps")
    print(f"Root Mean Squared Error:  {res['rmse_bps']} bps")
    print(f"Mean Model Slippage:      {res['mean_model_slippage_bps']} bps")
    print(f"Mean Realized Impact:     {res['mean_realized_impact_bps']} bps")
    print("\nBreakdown by Trade Size:")
    print(f"{'Size Bucket':<16} | {'Count':<6} | {'Model (bps)':<12} | {'Realized (bps)':<15} | {'MAE (bps)':<10}")
    print("-" * 70)
    for b in res["bucket_breakdown"]:
        print(f"{b['size_range']:<16} | {b['count']:<6} | {b['mean_model_bps']:<12} | {b['mean_realized_bps']:<15} | {b['mae_bps']:<10}")


def main():
    """Run validation across AAPL and XYZ_ILLIQ."""
    storage = DuckDBStorage(db_path="data/synthetic_depth.duckdb")

    print("=" * 75)
    print("  SYNTHETIC DEPTH & SLIPPAGE MODEL PROXY VALIDATION")
    print("=" * 75)

    # 1. Validate on AAPL (Liquid Large-Cap)
    aapl_res = run_validation_for_symbol(storage, "AAPL", "2026-07-06")
    print_symbol_results(aapl_res, "[1] AAPL (High-Liquidity Large-Cap)")

    # 2. Validate on XYZ_ILLIQ (Illiquid Small-Cap)
    illiq_res = run_validation_for_symbol(storage, "XYZ_ILLIQ", "2026-07-06")
    print_symbol_results(illiq_res, "[2] XYZ_ILLIQ (Illiquid / Wide-Spread Small-Cap)")

    storage.close()
    print("\n" + "=" * 75 + "\n")


if __name__ == "__main__":
    main()
