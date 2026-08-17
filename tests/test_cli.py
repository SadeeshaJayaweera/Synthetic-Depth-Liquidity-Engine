"""Tests for Ingestion, Classification, Metrics & Synthetic Depth CLI entrypoints."""

from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from synthetic_depth.ingestion.cli import main


def test_cli_backfill_dispatch(tmp_path):
    """Test CLI backfill command triggers HistoricalIngestor."""
    db_file = str(tmp_path / "cli_test.duckdb")
    test_args = [
        "backfill",
        "--symbol", "AAPL",
        "--start", "2026-07-06",
        "--end", "2026-07-06",
        "--db-path", db_file,
        "--api-key", "test_key",
    ]

    with patch("synthetic_depth.ingestion.cli.HistoricalIngestor") as mock_ingestor_cls:
        mock_instance = MagicMock()
        mock_instance.backfill.return_value = {
            "symbol": "AAPL",
            "start_date": "2026-07-06",
            "end_date": "2026-07-06",
            "days_processed": 1,
            "days_skipped": 0,
            "total_trades": 100,
            "total_quotes": 200,
            "status": "completed",
            "errors": [],
        }
        mock_ingestor_cls.return_value = mock_instance

        exit_code = main(test_args)
        assert exit_code == 0
        mock_instance.backfill.assert_called_once_with(
            symbol="AAPL",
            start_date="2026-07-06",
            end_date="2026-07-06",
            force=False,
        )


def test_cli_live_dispatch(tmp_path):
    """Test CLI live command triggers LiveIngestor."""
    db_file = str(tmp_path / "cli_live.duckdb")
    test_args = [
        "live",
        "--symbols", "AAPL,MSFT",
        "--db-path", db_file,
        "--api-key", "test_key",
        "--batch-size", "50",
    ]

    with patch("synthetic_depth.ingestion.cli.LiveIngestor") as mock_live_cls:
        mock_instance = MagicMock()
        mock_live_cls.return_value = mock_instance

        exit_code = main(test_args)
        assert exit_code == 0
        mock_instance.run.assert_called_once()


def test_cli_classify_dispatch(tmp_path):
    """Test CLI classify command triggers TradeClassifier."""
    db_file = str(tmp_path / "cli_classify.duckdb")
    test_args = [
        "classify",
        "--symbol", "AAPL",
        "--date", "2026-07-06",
        "--lag-ms", "50",
        "--db-path", db_file,
    ]

    with patch("synthetic_depth.ingestion.cli.TradeClassifier") as mock_classifier_cls:
        mock_instance = MagicMock()
        mock_instance.classify_symbol_date.return_value = pd.DataFrame([{"symbol": "AAPL", "price": 100.0}])
        mock_classifier_cls.return_value = mock_instance

        exit_code = main(test_args)
        assert exit_code == 0
        mock_instance.classify_symbol_date.assert_called_once_with(
            symbol="AAPL",
            date_str="2026-07-06",
            lag_ms=50.0,
            persist=True,
        )


def test_cli_metrics_dispatch(tmp_path):
    """Test CLI metrics command triggers LiquidityMetricsCalculator."""
    db_file = str(tmp_path / "cli_metrics.duckdb")
    test_args = [
        "metrics",
        "--symbol", "AAPL",
        "--date", "2026-07-06",
        "--bucket", "1min",
        "--db-path", db_file,
    ]

    with patch("synthetic_depth.ingestion.cli.LiquidityMetricsCalculator") as mock_calc_cls:
        mock_instance = MagicMock()
        mock_instance.calculate_symbol_date.return_value = pd.DataFrame([{
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "kyle_lambda": 0.0001,
            "amihud_ratio": 0.05,
            "replenishment_speed_ms": 120.0,
            "effective_spread": 0.02,
            "realized_spread": 0.01,
            "vpin": 0.25,
            "liquidity_score": 85.0,
        }])
        mock_calc_cls.return_value = mock_instance

        exit_code = main(test_args)
        assert exit_code == 0
        mock_instance.calculate_symbol_date.assert_called_once_with(
            symbol="AAPL",
            date_str="2026-07-06",
            time_bucket="1min",
            persist=True,
        )


def test_cli_depth_dispatch(tmp_path):
    """Test CLI depth command triggers SyntheticDepthEstimator."""
    db_file = str(tmp_path / "cli_depth.duckdb")
    test_args = [
        "depth",
        "--symbol", "AAPL",
        "--levels", "5",
        "--db-path", db_file,
    ]

    with patch("synthetic_depth.ingestion.cli.SyntheticDepthEstimator") as mock_est_cls:
        mock_instance = MagicMock()
        mock_book = MagicMock()
        mock_book.symbol = "AAPL"
        mock_book.timestamp = "2026-07-06 09:30:00"
        mock_book.bid_price = 150.00
        mock_book.ask_price = 150.02
        mock_book.spread = 0.02
        mock_book.mid_price = 150.01
        mock_book.kyle_lambda = 0.00002
        mock_book.replenishment_speed_ms = 100.0
        mock_book.data_type = "synthetic_estimate"
        mock_book.is_synthetic = True
        mock_book.disclaimer = "Synthetic disclaimer"
        mock_book.bids = []
        mock_book.asks = []
        mock_instance.get_latest_synthetic_book.return_value = mock_book
        mock_est_cls.return_value = mock_instance

        exit_code = main(test_args)
        assert exit_code == 0
        mock_instance.get_latest_synthetic_book.assert_called_once_with(
            symbol="AAPL",
            num_levels=5,
            tick_size=0.01,
        )


def test_cli_slippage_dispatch(tmp_path):
    """Test CLI slippage command triggers SlippageEstimator."""
    db_file = str(tmp_path / "cli_slippage.duckdb")
    test_args = [
        "slippage",
        "--symbol", "AAPL",
        "--shares", "2500",
        "--side", "BUY",
        "--db-path", db_file,
    ]

    with patch("synthetic_depth.ingestion.cli.SyntheticDepthEstimator") as mock_depth_cls, \
         patch("synthetic_depth.ingestion.cli.SlippageEstimator") as mock_slip_cls:
        
        mock_depth_inst = MagicMock()
        mock_book = MagicMock()
        mock_depth_inst.get_latest_synthetic_book.return_value = mock_book
        mock_depth_cls.return_value = mock_depth_inst

        mock_slip_inst = MagicMock()
        mock_estimate = MagicMock()
        mock_estimate.midpoint_price = 150.01
        mock_estimate.average_execution_price = 150.04
        mock_estimate.slippage_dollars = 0.03
        mock_estimate.slippage_bps = 2.0
        mock_estimate.half_spread_cost_bps = 0.67
        mock_estimate.impact_cost_bps = 1.33
        mock_estimate.levels_swept = 3
        mock_estimate.confidence_level.value = "HIGH"
        mock_estimate.confidence_score = 0.90
        mock_estimate.warnings = []
        mock_estimate.disclaimer = "Synthetic disclaimer"
        mock_slip_inst.estimate_slippage.return_value = mock_estimate
        mock_slip_cls.return_value = mock_slip_inst

        exit_code = main(test_args)
        assert exit_code == 0
        mock_slip_inst.estimate_slippage.assert_called_once_with(
            synthetic_book=mock_book,
            order_size=2500,
            side="BUY",
        )
