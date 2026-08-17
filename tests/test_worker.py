"""Tests for Background Worker daemon and scheduled jobs."""

import pandas as pd
import pytest

from synthetic_depth.storage.duckdb_layer import DuckDBStorage
from synthetic_depth.worker.worker import BackgroundMetricsWorker, NightlyGapFillWorker


@pytest.fixture
def temp_storage(tmp_path):
    """Isolated test DuckDB database."""
    db_file = tmp_path / "worker_test.duckdb"
    storage = DuckDBStorage(db_path=db_file)

    # Insert sample quotes
    quotes_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "bid_price": 150.00,
            "ask_price": 150.02,
            "bid_size": 200,
            "ask_size": 300,
            "exchange": "11",
            "quote_id": "q_w_1",
        }
    ])
    storage.insert_quotes_dataframe(quotes_df)

    # Insert sample trades
    trades_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:01"),
            "price": 150.02,
            "size": 100,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t_w_1",
        }
    ])
    storage.insert_trades_dataframe(trades_df)

    yield storage
    storage.close()


def test_background_metrics_worker_cycle(temp_storage):
    """Test background metrics worker processes unclassified trades and calculates metrics."""
    worker = BackgroundMetricsWorker(
        symbols=["AAPL"],
        storage=temp_storage,
        bucket="1min",
    )

    # Run cycle for test date
    worker.run_metric_cycle(target_date="2026-07-06")

    # Verify classified trades
    classified = temp_storage.get_classified_trades("AAPL")
    assert len(classified) == 1

    # Verify liquidity metrics
    metrics = temp_storage.get_liquidity_metrics("AAPL")
    assert len(metrics) >= 1
    assert "liquidity_score" in metrics.columns


def test_background_metrics_worker_empty_symbol_graceful(temp_storage):
    """Test worker handles symbols with no data without crashing."""
    worker = BackgroundMetricsWorker(
        symbols=["NONEXISTENT"],
        storage=temp_storage,
    )
    # Must execute cleanly without exception
    worker.run_metric_cycle(target_date="2026-07-06")


def test_nightly_gap_fill_worker_no_key(temp_storage):
    """Test gap fill worker handles missing API key gracefully."""
    worker = NightlyGapFillWorker(
        symbols=["AAPL"],
        storage=temp_storage,
        api_key="",
    )
    # Must skip gracefully
    worker.run_gap_fill(date_str="2026-07-06")
