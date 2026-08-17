"""Tests for Dashboard layout, chart generation, and components."""

import pandas as pd
import pytest
import plotly.graph_objects as go

from synthetic_depth.dashboard.app import create_dashboard_app
from synthetic_depth.dashboard.components import (
    create_liquidity_time_series,
    create_synthetic_depth_chart,
    get_watchlist_summary,
)
from synthetic_depth.microstructure.synthetic_book import SyntheticDepthEstimator
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


@pytest.fixture
def temp_storage(tmp_path):
    """Isolated temporary DuckDB database for dashboard tests."""
    db_file = tmp_path / "test_dash.duckdb"
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
            "quote_id": "q_dash_1",
        }
    ])
    storage.insert_quotes_dataframe(quotes_df)

    # Insert sample metrics
    metrics_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "kyle_lambda": 0.00002,
            "amihud_ratio": 0.001,
            "replenishment_speed_ms": 200.0,
            "effective_spread": 0.02,
            "realized_spread": 0.01,
            "vpin": 0.04,
            "liquidity_score": 85.0,
        }
    ])
    storage.insert_liquidity_metrics_dataframe(metrics_df)

    yield storage
    storage.close()


def test_create_synthetic_depth_chart():
    """Test synthetic depth ladder chart contains bids, asks, and synthetic distinction."""
    estimator = SyntheticDepthEstimator()
    book = estimator.build_synthetic_book(
        symbol="AAPL",
        bid_price=150.00,
        ask_price=150.02,
        bid_size=200,
        ask_size=300,
        num_levels=5,
    )

    fig = create_synthetic_depth_chart(book)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2  # Bids trace and Asks trace

    # Check annotation watermark
    annotations = [a.text for a in fig.layout.annotations]
    assert any("MODELED SYNTHETIC DEPTH" in text for text in annotations)


def test_create_liquidity_time_series():
    """Test liquidity time series chart with metric overlays."""
    metrics_df = pd.DataFrame([
        {
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "liquidity_score": 80.0,
            "kyle_lambda": 0.00002,
            "vpin": 0.05,
            "effective_spread": 0.01,
            "amihud_ratio": 0.001,
        },
        {
            "timestamp": pd.Timestamp("2026-07-06 09:31:00"),
            "liquidity_score": 85.0,
            "kyle_lambda": 0.000018,
            "vpin": 0.04,
            "effective_spread": 0.01,
            "amihud_ratio": 0.0008,
        },
    ])

    fig = create_liquidity_time_series(metrics_df, overlays=["kyle_lambda", "vpin"])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 3  # Score + Kyle Lambda + VPIN


def test_get_watchlist_summary(temp_storage):
    """Test watchlist data summary extraction."""
    summary = get_watchlist_summary(temp_storage)
    assert len(summary) >= 1
    assert summary[0]["symbol"] == "AAPL"
    assert "liquidity_score" in summary[0]
    assert "mid_price" in summary[0]


def test_create_dashboard_app():
    """Test Dash app instance creation and layout verification."""
    app = create_dashboard_app()
    assert app is not None
    assert app.layout is not None
