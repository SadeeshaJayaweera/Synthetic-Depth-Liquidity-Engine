"""Tests for LiveIngestor: message normalization, buffer batch flushing, and reconnection."""

import json
from unittest.mock import MagicMock
import pytest
from synthetic_depth.ingestion.live import LiveIngestor
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


@pytest.fixture
def temp_storage(tmp_path):
    """Provide an isolated temporary DuckDB storage instance."""
    db_file = tmp_path / "test_live.duckdb"
    storage = DuckDBStorage(db_path=db_file)
    yield storage
    storage.close()


def test_live_message_normalization_and_buffering(temp_storage):
    """Test incoming WebSocket trade (T) and quote (Q) message parsing and buffering."""
    ingestor = LiveIngestor(
        symbols=["AAPL"],
        api_key="test_key",
        storage=temp_storage,
        batch_size=10,
        flush_interval_seconds=10.0,
    )

    trade_msg = json.dumps([
        {
            "ev": "T",
            "sym": "AAPL",
            "x": 4,
            "i": "trade_ws_1",
            "z": 3,
            "p": 175.50,
            "s": 100,
            "c": [0, 12],
            "t": 1708245600123,
            "q": 101,
        }
    ])

    quote_msg = json.dumps([
        {
            "ev": "Q",
            "sym": "AAPL",
            "bx": 11,
            "bp": 175.45,
            "bs": 10,
            "ax": 12,
            "ap": 175.55,
            "as": 20,
            "c": 0,
            "t": 1708245600123,
            "q": 202,
        }
    ])

    ingestor.process_message(trade_msg)
    ingestor.process_message(quote_msg)

    assert len(ingestor._trades_buffer) == 1
    assert len(ingestor._quotes_buffer) == 1
    assert ingestor._trades_buffer[0]["symbol"] == "AAPL"
    assert ingestor._trades_buffer[0]["price"] == 175.50
    assert ingestor._quotes_buffer[0]["bid_price"] == 175.45

    # Flush manually
    flushed = ingestor.flush_buffers()
    assert flushed["trades"] == 1
    assert flushed["quotes"] == 1
    assert len(ingestor._trades_buffer) == 0
    assert len(ingestor._quotes_buffer) == 0

    counts = temp_storage.get_table_counts()
    assert counts["trades"] == 1
    assert counts["quotes"] == 1


def test_live_batch_size_auto_flush(temp_storage):
    """Test that reaching batch_size automatically flushes buffer to DuckDB."""
    ingestor = LiveIngestor(
        symbols=["MSFT"],
        api_key="test_key",
        storage=temp_storage,
        batch_size=2,
        flush_interval_seconds=60.0,
    )

    batch_msgs = json.dumps([
        {"ev": "T", "sym": "MSFT", "x": 1, "i": "m1", "p": 400.0, "s": 50, "t": 1708245600000},
        {"ev": "T", "sym": "MSFT", "x": 1, "i": "m2", "p": 400.5, "s": 75, "t": 1708245600100},
    ])

    ingestor.process_message(batch_msgs)

    # Buffer should have auto-flushed because length == batch_size (2)
    assert len(ingestor._trades_buffer) == 0
    counts = temp_storage.get_table_counts()
    assert counts["trades"] == 2
