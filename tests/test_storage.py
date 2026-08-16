"""Tests for DuckDB storage layer and schema creation."""

import pandas as pd
import pytest
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


def test_duckdb_in_memory_initialization():
    """Test initializing in-memory DuckDB and table creation."""
    storage = DuckDBStorage(db_path=":memory:")
    counts = storage.get_table_counts()
    assert counts["trades"] == 0
    assert counts["quotes"] == 0
    assert counts["ingestion_log"] == 0
    storage.close()


def test_duckdb_insert_and_query_trades():
    """Test inserting trade ticks and executing SQL query."""
    storage = DuckDBStorage(db_path=":memory:")

    df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 14:30:00"),
            "price": 150.25,
            "size": 100,
            "exchange": "11",
            "conditions": "12",
            "trade_id": "trade_1001",
        }
    ])

    inserted = storage.insert_trades_dataframe(df)
    assert inserted == 1

    counts = storage.get_table_counts()
    assert counts["trades"] == 1

    result_df = storage.get_trades("AAPL")
    assert len(result_df) == 1
    assert result_df.iloc[0]["price"] == 150.25
    assert result_df.iloc[0]["size"] == 100
    assert result_df.iloc[0]["trade_id"] == "trade_1001"
    storage.close()


def test_duckdb_quotes_and_ingestion_log():
    """Test inserting quote ticks and recording ingestion logs."""
    storage = DuckDBStorage(db_path=":memory:")

    q_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 14:30:00"),
            "bid_price": 150.20,
            "bid_size": 10,
            "ask_price": 150.30,
            "ask_size": 15,
            "exchange": "11/12",
            "quote_id": "quote_1001",
        }
    ])

    inserted = storage.insert_quotes_dataframe(q_df)
    assert inserted == 1

    storage.record_ingestion_log(symbol="AAPL", date_str="2026-07-06", trades_fetched=50, quotes_fetched=100)
    assert storage.is_date_ingested("AAPL", "2026-07-06") is True
    assert storage.is_date_ingested("AAPL", "2026-07-07") is False

    quotes_res = storage.get_quotes("AAPL")
    assert len(quotes_res) == 1
    assert quotes_res.iloc[0]["bid_price"] == 150.20

    counts = storage.get_table_counts()
    assert counts["quotes"] == 1
    assert counts["ingestion_log"] == 1
    storage.close()
