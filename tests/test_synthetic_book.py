"""Tests for Synthetic Depth Reconstruction and Execution Slippage Estimation."""

import pytest
from synthetic_depth.microstructure.synthetic_book import (
    ConfidenceLevel,
    SyntheticBookLevel,
    SyntheticOrderBook,
    SlippageEstimate,
    SyntheticDepthEstimator,
    SlippageEstimator,
)
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


@pytest.fixture
def temp_storage(tmp_path):
    """Isolated temporary DuckDB database."""
    db_file = tmp_path / "test_synth_book.duckdb"
    storage = DuckDBStorage(db_path=db_file)
    yield storage
    storage.close()


def test_synthetic_depth_estimator_construction():
    """Test synthetic depth curve correctly builds real touch + modeled outer levels."""
    estimator = SyntheticDepthEstimator()
    book = estimator.build_synthetic_book(
        symbol="AAPL",
        bid_price=150.00,
        ask_price=150.02,
        bid_size=500,
        ask_size=600,
        kyle_lambda=0.00002,
        replenishment_speed_ms=100.0,
        num_levels=5,
        tick_size=0.01,
    )

    assert book.symbol == "AAPL"
    assert book.mid_price == 150.01
    assert book.spread == 0.02
    assert book.data_type == "synthetic_estimate"
    assert book.is_synthetic is True
    assert len(book.bids) == 5
    assert len(book.asks) == 5

    # Level 1 is real NBBO touch
    assert book.bids[0].level == 1
    assert book.bids[0].price == 150.00
    assert book.bids[0].size == 500
    assert book.bids[0].is_synthetic is False

    assert book.asks[0].level == 1
    assert book.asks[0].price == 150.02
    assert book.asks[0].size == 600
    assert book.asks[0].is_synthetic is False

    # Outer levels are synthetic and monotonic
    for i in range(1, 5):
        assert book.bids[i].is_synthetic is True
        assert book.asks[i].is_synthetic is True
        assert book.bids[i].price < book.bids[i - 1].price
        assert book.asks[i].price > book.asks[i - 1].price
        assert book.bids[i].cumulative_size > book.bids[i - 1].cumulative_size
        assert book.asks[i].cumulative_size > book.asks[i - 1].cumulative_size


def test_slippage_estimator_small_order_touch_fill():
    """Test small order executing entirely within top-of-book NBBO size."""
    estimator = SyntheticDepthEstimator()
    book = estimator.build_synthetic_book(
        symbol="AAPL",
        bid_price=100.00,
        ask_price=100.02,
        bid_size=1000,
        ask_size=1000,
        kyle_lambda=0.00002,
        replenishment_speed_ms=50.0,
    )

    slippage_calc = SlippageEstimator()
    # Buy 200 shares (less than 1,000 at ask)
    estimate = slippage_calc.estimate_slippage(book, order_size=200, side="BUY")

    assert estimate.order_size == 200
    assert estimate.average_execution_price == 100.02
    assert estimate.levels_swept == 1
    assert estimate.unfilled_shares == 0
    # Slippage is exactly half-spread (100.02 - 100.01 = $0.01 = 1.0 bps on $100.01)
    assert pytest.approx(estimate.slippage_dollars, 0.001) == 0.01
    assert estimate.impact_cost_bps == 0.0
    assert estimate.confidence_level == ConfidenceLevel.HIGH
    assert estimate.data_type == "synthetic_estimate"
    assert estimate.is_synthetic is True


def test_slippage_estimator_large_order_sweeping_levels():
    """Test large order walking synthetic depth curve across multiple levels."""
    estimator = SyntheticDepthEstimator()
    book = estimator.build_synthetic_book(
        symbol="AAPL",
        bid_price=100.00,
        ask_price=100.02,
        bid_size=200,
        ask_size=200,
        kyle_lambda=0.00005,
        replenishment_speed_ms=500.0,
        num_levels=10,
    )

    slippage_calc = SlippageEstimator()
    # Buy 3,000 shares (exceeds touch size of 200)
    estimate = slippage_calc.estimate_slippage(book, order_size=3000, side="BUY")

    assert estimate.levels_swept > 1
    assert estimate.average_execution_price > 100.02
    assert estimate.slippage_bps > estimate.half_spread_cost_bps
    assert estimate.impact_cost_bps > 0.0


def test_slippage_estimator_sell_side():
    """Test sell order walking bid levels downwards."""
    estimator = SyntheticDepthEstimator()
    book = estimator.build_synthetic_book(
        symbol="AAPL",
        bid_price=100.00,
        ask_price=100.02,
        bid_size=300,
        ask_size=300,
        kyle_lambda=0.00005,
        num_levels=5,
    )

    slippage_calc = SlippageEstimator()
    # Sell 1,500 shares
    estimate = slippage_calc.estimate_slippage(book, order_size=1500, side="SELL")

    assert estimate.side == "SELL"
    assert estimate.average_execution_price < 100.00
    assert estimate.slippage_dollars > 0.0
    assert estimate.slippage_bps > 0.0


def test_slippage_estimator_oversize_order_and_warnings():
    """Test massive order exceeding depth triggers lower confidence and warnings."""
    estimator = SyntheticDepthEstimator()
    book = estimator.build_synthetic_book(
        symbol="ILLIQ",
        bid_price=10.00,
        ask_price=10.10,
        bid_size=50,
        ask_size=50,
        kyle_lambda=0.005,
        replenishment_speed_ms=5000.0,
        num_levels=5,
    )

    slippage_calc = SlippageEstimator()
    # Order for 50,000 shares on thin 50-share book
    estimate = slippage_calc.estimate_slippage(book, order_size=50000, side="BUY")

    assert estimate.confidence_level in [ConfidenceLevel.LOW, ConfidenceLevel.VERY_LOW]
    assert len(estimate.warnings) > 0
    assert any("Oversized" in w or "exceeds" in w for w in estimate.warnings)


def test_synthetic_depth_estimator_from_duckdb(temp_storage):
    """Test get_latest_synthetic_book queries DuckDB storage."""
    import pandas as pd
    temp_storage.insert_quotes_dataframe(pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "bid_price": 150.00,
            "ask_price": 150.02,
            "bid_size": 200,
            "ask_size": 300,
            "exchange": "11",
            "quote_id": "q_1",
        }
    ]))

    estimator = SyntheticDepthEstimator(storage=temp_storage)
    book = estimator.get_latest_synthetic_book("AAPL", num_levels=5)

    assert book is not None
    assert book.symbol == "AAPL"
    assert book.bid_price == 150.00
    assert book.ask_price == 150.02
    assert len(book.bids) == 5
