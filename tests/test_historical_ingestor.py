"""Tests for HistoricalIngestor: pagination, idempotency, rate limiting, and resume."""

from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from synthetic_depth.ingestion.historical import HistoricalIngestor
from synthetic_depth.ingestion.rest_client import MassiveRESTClient
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


@pytest.fixture
def temp_storage(tmp_path):
    """Provide an isolated temporary DuckDB storage instance."""
    db_file = tmp_path / "test_historical.duckdb"
    storage = DuckDBStorage(db_path=db_file)
    yield storage
    storage.close()


def test_pagination_handling(temp_storage):
    """Test multi-page cursor traversal for trades and quotes."""
    mock_client = MagicMock(spec=MassiveRESTClient)

    # Page 1 & Page 2 responses for trades
    trades_page1 = {
        "status": "OK",
        "next_url": "https://api.massive.com/v3/trades/AAPL?cursor=PAGE2_TRADES",
        "results": [
            {
                "id": "t1",
                "price": 150.0,
                "size": 100,
                "exchange": 11,
                "conditions": [12],
                "sip_timestamp": 1708245600000000000,
                "sequence_number": 1,
            },
            {
                "id": "t2",
                "price": 150.1,
                "size": 200,
                "exchange": 11,
                "conditions": [12],
                "sip_timestamp": 1708245600100000000,
                "sequence_number": 2,
            },
        ],
    }
    trades_page2 = {
        "status": "OK",
        "next_url": None,
        "results": [
            {
                "id": "t3",
                "price": 150.2,
                "size": 300,
                "exchange": 12,
                "conditions": [0],
                "sip_timestamp": 1708245600200000000,
                "sequence_number": 3,
            }
        ],
    }

    def fetch_trades_side_effect(ticker, timestamp=None, cursor_url=None, limit=50000):
        if cursor_url == "https://api.massive.com/v3/trades/AAPL?cursor=PAGE2_TRADES":
            return trades_page2
        return trades_page1

    mock_client.fetch_trades_page.side_effect = fetch_trades_side_effect

    # Quotes single page
    quotes_page1 = {
        "status": "OK",
        "next_url": None,
        "results": [
            {
                "bid_price": 149.9,
                "bid_size": 10,
                "bid_exchange": 11,
                "ask_price": 150.1,
                "ask_size": 20,
                "ask_exchange": 12,
                "sip_timestamp": 1708245600000000000,
                "sequence_number": 101,
            }
        ],
    }
    mock_client.fetch_quotes_page.return_value = quotes_page1

    ingestor = HistoricalIngestor(rest_client=mock_client, storage=temp_storage)
    summary = ingestor.backfill(symbol="AAPL", start_date="2026-07-06", end_date="2026-07-06")

    assert summary["status"] == "completed"
    assert summary["total_trades"] == 3
    assert summary["total_quotes"] == 1

    counts = temp_storage.get_table_counts()
    assert counts["trades"] == 3
    assert counts["quotes"] == 1
    assert counts["ingestion_log"] == 1


def test_idempotent_reingestion(temp_storage):
    """Test re-running ingestion does not create duplicate rows."""
    mock_client = MagicMock(spec=MassiveRESTClient)
    mock_client.fetch_trades_page.return_value = {
        "status": "OK",
        "next_url": None,
        "results": [
            {
                "id": "t100",
                "price": 200.0,
                "size": 50,
                "exchange": 4,
                "conditions": [0],
                "sip_timestamp": 1708245600000000000,
                "sequence_number": 1,
            }
        ],
    }
    mock_client.fetch_quotes_page.return_value = {
        "status": "OK",
        "next_url": None,
        "results": [
            {
                "bid_price": 199.9,
                "bid_size": 5,
                "bid_exchange": 4,
                "ask_price": 200.1,
                "ask_size": 10,
                "ask_exchange": 4,
                "sip_timestamp": 1708245600000000000,
                "sequence_number": 10,
            }
        ],
    }

    ingestor = HistoricalIngestor(rest_client=mock_client, storage=temp_storage)

    # First run
    summary1 = ingestor.backfill(symbol="AAPL", start_date="2026-07-06", end_date="2026-07-06")
    assert summary1["days_processed"] == 1
    assert summary1["days_skipped"] == 0

    counts1 = temp_storage.get_table_counts()
    assert counts1["trades"] == 1
    assert counts1["quotes"] == 1

    # Second run without force -> should skip via ingestion_log
    summary2 = ingestor.backfill(symbol="AAPL", start_date="2026-07-06", end_date="2026-07-06")
    assert summary2["days_processed"] == 0
    assert summary2["days_skipped"] == 1

    # Counts remain identical
    counts2 = temp_storage.get_table_counts()
    assert counts2["trades"] == 1
    assert counts2["quotes"] == 1

    # Third run with force=True -> INSERT OR IGNORE avoids primary key duplicates
    summary3 = ingestor.backfill(symbol="AAPL", start_date="2026-07-06", end_date="2026-07-06", force=True)
    assert summary3["days_processed"] == 1
    counts3 = temp_storage.get_table_counts()
    assert counts3["trades"] == 1
    assert counts3["quotes"] == 1


def test_partial_failure_recovery(temp_storage):
    """Test resuming after failure in a multi-day date range."""
    mock_client = MagicMock(spec=MassiveRESTClient)

    # Monday 2026-07-06 succeeds, Tuesday 2026-07-07 fails
    call_count = {"val": 0}

    def fetch_trades_mock(ticker, timestamp=None, cursor_url=None, limit=50000):
        if timestamp == "2026-07-07":
            raise RuntimeError("Temporary Network Failure")
        return {
            "status": "OK",
            "next_url": None,
            "results": [
                {
                    "id": f"trade_{timestamp}",
                    "price": 100.0,
                    "size": 10,
                    "exchange": 1,
                    "conditions": [],
                    "sip_timestamp": 1708245600000000000,
                    "sequence_number": 1,
                }
            ],
        }

    mock_client.fetch_trades_page.side_effect = fetch_trades_mock
    mock_client.fetch_quotes_page.return_value = {"status": "OK", "next_url": None, "results": []}

    ingestor = HistoricalIngestor(rest_client=mock_client, storage=temp_storage)

    # First attempt: 2026-07-06 succeeds, 2026-07-07 fails
    summary1 = ingestor.backfill(symbol="AAPL", start_date="2026-07-06", end_date="2026-07-07")
    assert summary1["status"] == "partial_error"
    assert summary1["days_processed"] == 1
    assert temp_storage.is_date_ingested("AAPL", "2026-07-06") is True
    assert temp_storage.is_date_ingested("AAPL", "2026-07-07") is False

    # Second attempt: network fixed -> skips 2026-07-06 and finishes 2026-07-07
    def fetch_trades_mock_fixed(ticker, timestamp=None, cursor_url=None, limit=50000):
        return {
            "status": "OK",
            "next_url": None,
            "results": [
                {
                    "id": f"trade_{timestamp}",
                    "price": 100.0,
                    "size": 10,
                    "exchange": 1,
                    "conditions": [],
                    "sip_timestamp": 1708245600000000000,
                    "sequence_number": 1,
                }
            ],
        }

    mock_client.fetch_trades_page.side_effect = fetch_trades_mock_fixed
    summary2 = ingestor.backfill(symbol="AAPL", start_date="2026-07-06", end_date="2026-07-07")
    assert summary2["status"] == "completed"
    assert summary2["days_skipped"] == 1
    assert summary2["days_processed"] == 1
    assert temp_storage.is_date_ingested("AAPL", "2026-07-07") is True
