"""Tests for core liquidity metrics: Kyle's Lambda, Amihud, Replenishment, Spreads, VPIN, and LiquidityScore."""

import numpy as np
import pandas as pd
import pytest

from synthetic_depth.microstructure.classification import TradeDirection, ClassificationMethod
from synthetic_depth.microstructure.metrics import (
    compute_kyle_lambda,
    compute_amihud_ratio,
    compute_replenishment_speed,
    compute_spreads,
    compute_vpin,
    compute_liquidity_score,
    LiquidityMetricsCalculator,
)
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


@pytest.fixture
def temp_storage(tmp_path):
    """Isolated temporary DuckDB database for liquidity metrics tests."""
    db_file = tmp_path / "test_metrics.duckdb"
    storage = DuckDBStorage(db_path=db_file)
    yield storage
    storage.close()


def test_kyle_lambda_price_impact():
    """Test Kyle's Lambda detects positive price response to signed order flow."""
    # 5 1-minute buckets where positive OFI drives higher price
    records = []
    base_time = pd.Timestamp("2026-07-06 09:30:00")
    for minute in range(10):
        t_start = base_time + pd.Timedelta(minutes=minute)
        # Net buy flow: 2 buys of size 500, 1 sell of size 100 -> OFI = +900
        records.append({"symbol": "AAPL", "timestamp": t_start + pd.Timedelta(seconds=5), "price": 100.0 + minute * 0.10, "size": 500, "direction": "BUY"})
        records.append({"symbol": "AAPL", "timestamp": t_start + pd.Timedelta(seconds=30), "price": 100.0 + minute * 0.10 + 0.05, "size": 500, "direction": "BUY"})
        records.append({"symbol": "AAPL", "timestamp": t_start + pd.Timedelta(seconds=50), "price": 100.0 + minute * 0.10 + 0.05, "size": 100, "direction": "SELL"})

    df = pd.DataFrame(records)
    kyle_series = compute_kyle_lambda(df, time_bucket="1min", window=5)

    assert not kyle_series.empty
    # Kyle lambda values must be non-negative
    assert (kyle_series >= 0.0).all()


def test_amihud_ratio_calculation():
    """Test Amihud illiquidity ratio formula."""
    trades_df = pd.DataFrame([
        # Bucket 1: Open 100, Close 101 (delta 1.0%), Volume = 100 shares * $100 = $10,000
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:05"), "price": 100.0, "size": 50, "direction": "BUY"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:55"), "price": 101.0, "size": 50, "direction": "BUY"},
        # Bucket 2: Open 101, Close 101 (delta 0.0%), Volume = $10,100
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:31:05"), "price": 101.0, "size": 50, "direction": "BUY"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:31:55"), "price": 101.0, "size": 50, "direction": "BUY"},
    ])

    amihud = compute_amihud_ratio(trades_df, time_bucket="1min")
    assert len(amihud) == 2
    # Bucket 1 has positive return -> Amihud > 0
    assert amihud.iloc[0] > 0
    # Bucket 2 has 0 return -> Amihud == 0
    assert amihud.iloc[1] == 0.0


def test_replenishment_speed_deep_vs_shallow():
    """Test quote replenishment speed distinguishes deep resilient book from shallow slow book."""
    t_trade = pd.Timestamp("2026-07-06 09:30:05")

    # Trades with large print at 09:30:05
    trades_df = pd.DataFrame([
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:01"), "price": 150.0, "size": 10},
        {"symbol": "AAPL", "timestamp": t_trade, "price": 150.0, "size": 1000},  # Large print
    ])

    # Deep Book: Pre-trade depth = 100, drops to 10 at trade, recovers to 100 at 09:30:05.050 (50ms)
    deep_quotes = pd.DataFrame([
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:02.000"), "bid_size": 50, "ask_size": 50},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:05.000"), "bid_size": 5, "ask_size": 5},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:05.050"), "bid_size": 50, "ask_size": 50},
    ])

    # Shallow Book: Takes 3000ms to recover
    shallow_quotes = pd.DataFrame([
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:02.000"), "bid_size": 50, "ask_size": 50},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:05.000"), "bid_size": 5, "ask_size": 5},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:08.000"), "bid_size": 50, "ask_size": 50},
    ])

    deep_speed = compute_replenishment_speed(trades_df, deep_quotes, size_percentile=50.0, window_sec=5.0)
    shallow_speed = compute_replenishment_speed(trades_df, shallow_quotes, size_percentile=50.0, window_sec=5.0)

    assert deep_speed <= 100.0
    assert shallow_speed >= 2500.0
    assert deep_speed < shallow_speed


def test_spread_decomposition_effective_and_realized():
    """Test effective spread and 5-min realized spread calculations."""
    classified_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "price": 100.08,
            "mid_price": 100.05,
            "direction": "BUY",
        }
    ])

    # Quote 5 minutes later has midpoint = 100.07
    quotes_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:35:00"),
            "bid_price": 100.06,
            "ask_price": 100.08,  # mid = 100.07
        }
    ])

    spreads = compute_spreads(classified_df, quotes_df, delta_sec=300.0)

    # Effective spread = 2 * (100.08 - 100.05) = 0.06
    assert np.isclose(spreads.iloc[0]["effective_spread"], 0.06)
    # Realized spread = 2 * (100.08 - 100.07) = 0.02
    assert np.isclose(spreads.iloc[0]["realized_spread"], 0.02)


def test_vpin_calculation_bounds():
    """Test VPIN output is strictly bounded in [0, 1] and reflects flow toxicity."""
    # Generate 100 trades with strong buy imbalance
    records = []
    base_time = pd.Timestamp("2026-07-06 09:30:00")
    for i in range(100):
        records.append({
            "symbol": "AAPL",
            "timestamp": base_time + pd.Timedelta(seconds=i * 5),
            "price": 100.0 + i * 0.02,
            "size": 100,
        })

    vpin_df = compute_vpin(pd.DataFrame(records), num_buckets=10, window=5)
    assert not vpin_df.empty
    assert (vpin_df["vpin"] >= 0.0).all()
    assert (vpin_df["vpin"] <= 1.0).all()


def test_liquidity_score_normalization():
    """Test composite LiquidityScore normalization bounds and behavior."""
    # Deep profile: low lambda, low amihud, fast replenishment, narrow spread, low vpin
    deep_metrics = pd.DataFrame([{
        "kyle_lambda": 0.00001,
        "amihud_ratio": 0.01,
        "replenishment_speed_ms": 50.0,
        "effective_spread": 0.01,
        "vpin": 0.10,
    }, {
        "kyle_lambda": 0.00002,
        "amihud_ratio": 0.02,
        "replenishment_speed_ms": 60.0,
        "effective_spread": 0.015,
        "vpin": 0.12,
    }])

    # Shallow profile: high lambda, high amihud, slow replenishment, wide spread, high vpin
    shallow_metrics = pd.DataFrame([{
        "kyle_lambda": 0.005,
        "amihud_ratio": 5.0,
        "replenishment_speed_ms": 4000.0,
        "effective_spread": 0.20,
        "vpin": 0.75,
    }, {
        "kyle_lambda": 0.006,
        "amihud_ratio": 6.0,
        "replenishment_speed_ms": 4500.0,
        "effective_spread": 0.25,
        "vpin": 0.80,
    }])

    combined = pd.concat([deep_metrics, shallow_metrics], ignore_index=True)
    scores = compute_liquidity_score(combined)

    assert len(scores) == 4
    assert (scores >= 0.0).all()
    assert (scores <= 100.0).all()
    # Deep records score higher than shallow records
    assert scores.iloc[0] > scores.iloc[2]
    assert scores.iloc[1] > scores.iloc[3]


def test_liquidity_metrics_calculator_duckdb_pipeline(temp_storage):
    """Test full pipeline: raw ingest -> classify -> calculate metrics -> DuckDB persistence."""
    # Insert raw trades and quotes
    trades_df = pd.DataFrame([
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:05"), "price": 150.08, "size": 100, "exchange": "11", "conditions": "", "trade_id": "t1"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:30"), "price": 150.02, "size": 100, "exchange": "11", "conditions": "", "trade_id": "t2"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:31:05"), "price": 150.10, "size": 200, "exchange": "11", "conditions": "", "trade_id": "t3"},
    ])
    quotes_df = pd.DataFrame([
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:00"), "bid_price": 150.00, "ask_price": 150.10, "bid_size": 20, "ask_size": 20, "exchange": "11/12", "quote_id": "q1"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:31:00"), "bid_price": 150.05, "ask_price": 150.15, "bid_size": 20, "ask_size": 20, "exchange": "11/12", "quote_id": "q2"},
    ])
    temp_storage.insert_trades_dataframe(trades_df)
    temp_storage.insert_quotes_dataframe(quotes_df)

    from synthetic_depth.microstructure.classification import TradeClassifier
    classifier = TradeClassifier(storage=temp_storage)
    classifier.classify_symbol_date("AAPL", "2026-07-06", persist=True)

    calc = LiquidityMetricsCalculator(storage=temp_storage)
    metrics_df = calc.calculate_symbol_date(symbol="AAPL", date_str="2026-07-06", time_bucket="1min", persist=True)

    assert not metrics_df.empty
    assert "liquidity_score" in metrics_df.columns
    assert "kyle_lambda" in metrics_df.columns
    assert "vpin" in metrics_df.columns

    counts = temp_storage.get_table_counts()
    assert counts["liquidity_metrics"] == len(metrics_df)

    # Query from DuckDB
    saved_metrics = temp_storage.get_liquidity_metrics("AAPL")
    assert len(saved_metrics) == len(metrics_df)
