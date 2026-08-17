"""Tests for smoke test workflow and mock execution."""

from unittest.mock import MagicMock, patch
import pytest
from scripts.smoke_test import run_smoke_test


def test_smoke_test_missing_key():
    """Smoke test should return False when no valid key is set."""
    with patch("scripts.smoke_test.get_settings") as mock_settings:
        mock_instance = MagicMock()
        mock_instance.has_valid_api_key = False
        mock_instance.duckdb_path = "data/synthetic_depth.duckdb"
        mock_instance.log_level = "INFO"
        mock_settings.return_value = mock_instance

        success = run_smoke_test(ticker="AAPL")
        assert success is False


def test_smoke_test_mock_success():
    """Smoke test should return True when Massive API returns valid aggregate bars."""
    with patch("scripts.smoke_test.get_settings") as mock_settings, \
         patch("scripts.smoke_test.MassiveRESTClient") as mock_client_cls:
        
        mock_instance = MagicMock()
        mock_instance.has_valid_api_key = True
        mock_instance.massive_api_key = "test_valid_key"
        mock_instance.duckdb_path = "data/synthetic_depth.duckdb"
        mock_instance.log_level = "INFO"
        mock_settings.return_value = mock_instance

        mock_client = MagicMock()
        mock_client.get_previous_close_agg.return_value = [
            {
                "T": "AAPL",
                "o": 180.0,
                "h": 182.5,
                "l": 179.5,
                "c": 181.2,
                "v": 45000000,
                "vw": 181.0,
                "t": 1708245600000,
            }
        ]
        mock_client_cls.return_value = mock_client

        success = run_smoke_test(ticker="AAPL")
        assert success is True
        mock_client.get_previous_close_agg.assert_called_once_with("AAPL")
