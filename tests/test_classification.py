"""Tests for trade classification algorithms: Lee-Ready, Tick Rule, and Bulk Volume Classification (BVC)."""

from datetime import datetime
import numpy as np
import pandas as pd
import pytest

from synthetic_depth.microstructure.classification import (
    TradeDirection,
    ClassificationMethod,
    TradeClassifier,
    classify_trades_lee_ready,
    classify_bulk_volume_bvc,
)
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


@pytest.fixture
def temp_storage(tmp_path):
    """Isolated temporary DuckDB database for classification tests."""
    db_file = tmp_path / "test_classification.duckdb"
    storage = DuckDBStorage(db_path=db_file)
    yield storage
    storage.close()


def test_lee_ready_quote_rule_above_and_below_midpoint():
    """Test trade price > midpoint is BUY and < midpoint is SELL."""
    quotes_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "bid_price": 100.00,
            "ask_price": 100.10,
            "bid_size": 10,
            "ask_size": 10,
            "exchange": "11/12",
            "quote_id": "q1",
        }
    ])
    # Midpoint = 100.05

    trades_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:01"),
            "price": 100.08,  # > 100.05 -> BUY
            "size": 100,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t1",
        },
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:02"),
            "price": 100.02,  # < 100.05 -> SELL
            "size": 200,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t2",
        },
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:03"),
            "price": 100.15,  # > ask -> BUY
            "size": 50,
            "exchange": "12",
            "conditions": "",
            "trade_id": "t3",
        },
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:04"),
            "price": 99.95,  # < bid -> SELL
            "size": 75,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t4",
        },
    ])

    classified = classify_trades_lee_ready(trades_df, quotes_df)

    assert len(classified) == 4
    assert classified.iloc[0]["direction"] == TradeDirection.BUY.value
    assert classified.iloc[0]["classification_method"] == ClassificationMethod.QUOTE_RULE.value
    assert classified.iloc[0]["mid_price"] == 100.05

    assert classified.iloc[1]["direction"] == TradeDirection.SELL.value
    assert classified.iloc[1]["classification_method"] == ClassificationMethod.QUOTE_RULE.value

    assert classified.iloc[2]["direction"] == TradeDirection.BUY.value
    assert classified.iloc[3]["direction"] == TradeDirection.SELL.value


def test_lee_ready_tick_rule_at_midpoint():
    """Test trades executed exactly at midpoint fall back to the Tick Test."""
    quotes_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "bid_price": 100.00,
            "ask_price": 100.10,  # mid = 100.05
            "bid_size": 10,
            "ask_size": 10,
            "exchange": "11/12",
            "quote_id": "q1",
        }
    ])

    trades_df = pd.DataFrame([
        # Trade 1: price = 100.00 (below mid -> SELL)
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:01"),
            "price": 100.00,
            "size": 100,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t1",
        },
        # Trade 2: price = 100.05 (at mid, prior price was 100.00 -> uptick -> BUY)
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:02"),
            "price": 100.05,
            "size": 50,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t2",
        },
        # Trade 3: price = 100.05 (at mid, prior was 100.05 -> zero-uptick -> BUY)
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:03"),
            "price": 100.05,
            "size": 50,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t3",
        },
        # Trade 4: price = 100.10 (above mid -> BUY)
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:04"),
            "price": 100.10,
            "size": 100,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t4",
        },
        # Trade 5: price = 100.05 (at mid, prior price 100.10 -> downtick -> SELL)
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:05"),
            "price": 100.05,
            "size": 80,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t5",
        },
        # Trade 6: price = 100.05 (at mid, zero-downtick -> SELL)
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:06"),
            "price": 100.05,
            "size": 40,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t6",
        },
    ])

    classified = classify_trades_lee_ready(trades_df, quotes_df)

    assert classified.iloc[0]["direction"] == TradeDirection.SELL.value
    assert classified.iloc[0]["classification_method"] == ClassificationMethod.QUOTE_RULE.value

    assert classified.iloc[1]["direction"] == TradeDirection.BUY.value
    assert classified.iloc[1]["classification_method"] == ClassificationMethod.TICK_RULE.value

    assert classified.iloc[2]["direction"] == TradeDirection.BUY.value
    assert classified.iloc[2]["classification_method"] == ClassificationMethod.TICK_RULE.value

    assert classified.iloc[3]["direction"] == TradeDirection.BUY.value
    assert classified.iloc[3]["classification_method"] == ClassificationMethod.QUOTE_RULE.value

    assert classified.iloc[4]["direction"] == TradeDirection.SELL.value
    assert classified.iloc[4]["classification_method"] == ClassificationMethod.TICK_RULE.value

    assert classified.iloc[5]["direction"] == TradeDirection.SELL.value
    assert classified.iloc[5]["classification_method"] == ClassificationMethod.TICK_RULE.value


def test_lee_ready_first_trade_and_missing_quote_edge_cases():
    """Test first trade of day without prior ticks and trades with missing quote data."""
    # Quote mid = 100.00
    quotes_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "bid_price": 99.95,
            "ask_price": 100.05,
            "bid_size": 10,
            "ask_size": 10,
            "exchange": "11/12",
            "quote_id": "q1",
        }
    ])

    # First trade is exactly at midpoint with no previous tick
    trades_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:01"),
            "price": 100.00,
            "size": 100,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t1",
        },
        # Second trade is at midpoint, same price as first trade (no prior tick direction)
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:02"),
            "price": 100.00,
            "size": 100,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t2",
        },
        # Third trade is uptick at 100.01 (above mid -> BUY)
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:03"),
            "price": 100.01,
            "size": 100,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t3",
        },
    ])

    classified = classify_trades_lee_ready(trades_df, quotes_df)

    assert classified.iloc[0]["direction"] == TradeDirection.UNKNOWN.value
    assert classified.iloc[0]["classification_method"] == ClassificationMethod.UNKNOWN.value

    assert classified.iloc[1]["direction"] == TradeDirection.UNKNOWN.value

    assert classified.iloc[2]["direction"] == TradeDirection.BUY.value
    assert classified.iloc[2]["classification_method"] == ClassificationMethod.QUOTE_RULE.value


def test_lee_ready_pure_tick_rule_when_no_quotes_available():
    """Test classification when no quote feed is available (pure Tick Test)."""
    trades_df = pd.DataFrame([
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:01"), "price": 150.00, "size": 10, "exchange": "1", "conditions": "", "trade_id": "t1"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:02"), "price": 150.05, "size": 10, "exchange": "1", "conditions": "", "trade_id": "t2"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:03"), "price": 150.05, "size": 10, "exchange": "1", "conditions": "", "trade_id": "t3"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:04"), "price": 149.95, "size": 10, "exchange": "1", "conditions": "", "trade_id": "t4"},
    ])

    classified = classify_trades_lee_ready(trades_df, quotes_df=pd.DataFrame())

    assert classified.iloc[0]["direction"] == TradeDirection.UNKNOWN.value
    assert classified.iloc[1]["direction"] == TradeDirection.BUY.value
    assert classified.iloc[1]["classification_method"] == ClassificationMethod.TICK_RULE.value
    assert classified.iloc[2]["direction"] == TradeDirection.BUY.value
    assert classified.iloc[3]["direction"] == TradeDirection.SELL.value


def test_lee_ready_quote_reporting_lag():
    """Test that quote reporting lag (tau) correctly matches the quote prior to (t - lag_ms)."""
    quotes_df = pd.DataFrame([
        # Quote 1 at 09:30:00.000: mid = 100.05
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00.000"),
            "bid_price": 100.00,
            "ask_price": 100.10,
            "bid_size": 10,
            "ask_size": 10,
            "exchange": "11",
            "quote_id": "q1",
        },
        # Quote 2 at 09:30:01.000: mid = 100.25
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:01.000"),
            "bid_price": 100.20,
            "ask_price": 100.30,
            "bid_size": 10,
            "ask_size": 10,
            "exchange": "11",
            "quote_id": "q2",
        },
    ])

    # Trade at 09:30:01.050 with price 100.15
    trades_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:01.050"),
            "price": 100.15,
            "size": 100,
            "exchange": "11",
            "conditions": "",
            "trade_id": "t1",
        }
    ])

    # Without lag (lag_ms = 0): matches Quote 2 (mid = 100.25) -> price 100.15 < 100.25 -> SELL
    no_lag = classify_trades_lee_ready(trades_df, quotes_df, lag_ms=0.0)
    assert no_lag.iloc[0]["mid_price"] == 100.25
    assert no_lag.iloc[0]["direction"] == TradeDirection.SELL.value

    # With lag_ms = 100ms: threshold is 09:30:00.950 -> matches Quote 1 (mid = 100.05) -> price 100.15 > 100.05 -> BUY
    with_lag = classify_trades_lee_ready(trades_df, quotes_df, lag_ms=100.0)
    assert with_lag.iloc[0]["mid_price"] == 100.05
    assert with_lag.iloc[0]["direction"] == TradeDirection.BUY.value


def test_bulk_volume_classification_bvc():
    """Test Bulk Volume Classification (BVC) calculates buy and sell volumes accurately."""
    trades_df = pd.DataFrame([
        # Minute 1: open 100, close 102 (positive delta) -> buy_ratio > 0.5
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:10"), "price": 100.0, "size": 100},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:50"), "price": 102.0, "size": 200},
        # Minute 2: open 102, close 99 (negative delta) -> buy_ratio < 0.5
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:31:10"), "price": 102.0, "size": 150},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:31:50"), "price": 99.0, "size": 250},
    ])

    bvc = classify_bulk_volume_bvc(trades_df, time_bucket="1min", window=2)
    assert len(bvc) == 2
    assert bvc.iloc[0]["volume"] == 300
    assert bvc.iloc[1]["volume"] == 400

    # Total volume equals buy_volume + sell_volume
    assert np.isclose(bvc.iloc[0]["buy_volume"] + bvc.iloc[0]["sell_volume"], 300)
    assert np.isclose(bvc.iloc[1]["buy_volume"] + bvc.iloc[1]["sell_volume"], 400)

    # Minute 2 (downturn) has lower buy_ratio than minute 1
    assert bvc.iloc[1]["buy_ratio"] < bvc.iloc[0]["buy_ratio"]


def test_trade_classifier_orchestrator_and_duckdb_persistence(temp_storage):
    """Test full pipeline: raw ingest in DuckDB -> classify -> persist to classified_trades -> verify stats."""
    # Insert raw trades
    raw_trades = pd.DataFrame([
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:01"), "price": 150.10, "size": 100, "exchange": "11", "conditions": "", "trade_id": "tr_1"},
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:02"), "price": 149.90, "size": 200, "exchange": "11", "conditions": "", "trade_id": "tr_2"},
    ])
    temp_storage.insert_trades_dataframe(raw_trades)

    # Insert raw quotes (mid = 150.00)
    raw_quotes = pd.DataFrame([
        {"symbol": "AAPL", "timestamp": pd.Timestamp("2026-07-06 09:30:00"), "bid_price": 149.95, "ask_price": 150.05, "bid_size": 10, "ask_size": 10, "exchange": "11/12", "quote_id": "qu_1"}
    ])
    temp_storage.insert_quotes_dataframe(raw_quotes)

    classifier = TradeClassifier(storage=temp_storage)
    classified = classifier.classify_symbol_date(symbol="AAPL", date_str="2026-07-06", persist=True)

    assert len(classified) == 2
    assert temp_storage.get_table_counts()["classified_trades"] == 2

    # Query classified trades
    saved = temp_storage.get_classified_trades("AAPL")
    assert len(saved) == 2
    assert saved.iloc[0]["direction"] == TradeDirection.BUY.value
    assert saved.iloc[1]["direction"] == TradeDirection.SELL.value

    # Check stats
    stats = temp_storage.get_classification_stats("AAPL", date_str="2026-07-06")
    assert stats["total_trades"] == 2
    assert stats["total_volume"] == 300
    assert stats["buy_trades"] == 1
    assert stats["sell_trades"] == 1
    assert stats["buy_pct"] == 50.0
    assert stats["sell_pct"] == 50.0
    assert stats["buy_volume"] == 100
    assert stats["sell_volume"] == 200
