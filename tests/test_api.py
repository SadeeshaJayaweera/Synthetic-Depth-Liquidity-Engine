"""Integration tests for Synthetic Depth Engine FastAPI service."""

from datetime import datetime
from unittest.mock import patch
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from synthetic_depth.api.app import app
from synthetic_depth.api.security import get_rate_limiter
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


@pytest.fixture(autouse=True)
def reset_limiter():
    """Reset the in-memory rate limiter before each test."""
    get_rate_limiter().reset()


@pytest.fixture
def client_with_db(tmp_path):
    """Test client configured with an isolated test DuckDB database populated with AAPL data."""
    db_file = tmp_path / "api_test.duckdb"
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
            "quote_id": "q_test_1",
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
            "trade_id": "t_test_1",
        }
    ])
    storage.insert_trades_dataframe(trades_df)

    # Insert sample liquidity metrics
    metrics_df = pd.DataFrame([
        {
            "symbol": "AAPL",
            "timestamp": pd.Timestamp("2026-07-06 09:30:00"),
            "kyle_lambda": 0.000025,
            "amihud_ratio": 0.0012,
            "replenishment_speed_ms": 150.0,
            "effective_spread": 0.02,
            "realized_spread": 0.01,
            "vpin": 0.05,
            "liquidity_score": 88.5,
        }
    ])
    storage.insert_liquidity_metrics_dataframe(metrics_df)

    with patch("synthetic_depth.api.routes.health.DuckDBStorage", return_value=storage), \
         patch("synthetic_depth.api.routes.liquidity.DuckDBStorage", return_value=storage), \
         patch("synthetic_depth.api.routes.synthetic_depth.DuckDBStorage", return_value=storage), \
         patch("synthetic_depth.api.routes.slippage.DuckDBStorage", return_value=storage), \
         patch("synthetic_depth.api.routes.websocket.DuckDBStorage", return_value=storage):
        
        with TestClient(app) as test_client:
            yield test_client

    storage.close()


def test_health_endpoint(client_with_db):
    """Test /health endpoint does not require authentication and reports status."""
    response = client_with_db.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["duckdb_connected"] is True
    assert "table_counts" in data
    assert data["table_counts"]["trades"] >= 1


def test_auth_rejection_on_missing_or_invalid_key(client_with_db):
    """Test protected endpoints reject requests lacking valid X-API-Key."""
    # Missing header
    res1 = client_with_db.get("/symbols/AAPL/liquidity-score")
    assert res1.status_code == 401
    assert "Invalid or missing API key" in res1.json()["detail"]

    # Invalid header
    res2 = client_with_db.get("/symbols/AAPL/liquidity-score", headers={"X-API-Key": "invalid-key"})
    assert res2.status_code == 401


def test_rate_limiter_enforcement(client_with_db):
    """Test rate limiter returns 429 when max requests per minute is exceeded."""
    limiter = get_rate_limiter()
    limiter.requests_per_minute = 3

    headers = {"X-API-Key": "dev-test-key-123"}
    for _ in range(3):
        res = client_with_db.get("/symbols/AAPL/liquidity-score", headers=headers)
        assert res.status_code == 200

    # 4th request must be blocked
    blocked = client_with_db.get("/symbols/AAPL/liquidity-score", headers=headers)
    assert blocked.status_code == 429
    assert "rate limit exceeded" in blocked.json()["detail"].lower()
    assert blocked.headers.get("Retry-After") == "60"


def test_liquidity_score_endpoint(client_with_db):
    """Test /symbols/{symbol}/liquidity-score returns score and breakdown."""
    headers = {"X-API-Key": "dev-test-key-123"}
    res = client_with_db.get("/symbols/AAPL/liquidity-score", headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert data["symbol"] == "AAPL"
    assert data["liquidity_score"] == 88.5
    assert "components" in data
    assert data["components"]["kyle_lambda"] == 0.000025
    assert data["components"]["replenishment_speed_ms"] == 150.0
    assert data["data_type"] == "microstructure_metrics"


def test_synthetic_depth_endpoint(client_with_db):
    """Test /symbols/{symbol}/synthetic-depth returns modeled order book with disclaimers."""
    headers = {"X-API-Key": "dev-test-key-123"}
    res = client_with_db.get("/symbols/AAPL/synthetic-depth?levels=5", headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert data["symbol"] == "AAPL"
    assert data["bid_price"] == 150.00
    assert data["ask_price"] == 150.02
    assert data["data_type"] == "synthetic_estimate"
    assert data["is_synthetic"] is True
    assert "DISCLAIMER" in data["disclaimer"]
    assert len(data["bids"]) == 5
    assert len(data["asks"]) == 5

    # Level 1 is touch
    assert data["bids"][0]["level"] == 1
    assert data["bids"][0]["is_synthetic"] is False
    # Level 2 is synthetic
    assert data["bids"][1]["level"] == 2
    assert data["bids"][1]["is_synthetic"] is True


def test_slippage_estimate_endpoint(client_with_db):
    """Test POST /symbols/{symbol}/slippage-estimate calculates execution slippage and confidence."""
    headers = {"X-API-Key": "dev-test-key-123"}
    payload = {"size": 2500, "direction": "BUY"}
    res = client_with_db.post("/symbols/AAPL/slippage-estimate", json=payload, headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert data["symbol"] == "AAPL"
    assert data["direction"] == "BUY"
    assert data["order_size"] == 2500
    assert data["average_execution_price"] >= 150.02
    assert data["slippage_bps"] > 0
    assert "confidence_level" in data
    assert "confidence_score" in data
    assert data["data_type"] == "synthetic_estimate"
    assert data["is_synthetic"] is True
    assert "DISCLAIMER" in data["disclaimer"]


def test_metrics_history_endpoint(client_with_db):
    """Test /symbols/{symbol}/metrics/history returns time series."""
    headers = {"X-API-Key": "dev-test-key-123"}
    res = client_with_db.get("/symbols/AAPL/metrics/history?limit=10", headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert data["symbol"] == "AAPL"
    assert data["total_records"] == 1
    assert len(data["data"]) == 1
    assert data["data"][0]["liquidity_score"] == 88.5


def test_websocket_live_liquidity_stream(client_with_db):
    """Test WebSocket connection and receiving live liquidity snapshot."""
    with client_with_db.websocket_connect("/ws/symbols/AAPL/live-liquidity?api_key=dev-test-key-123") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "live_liquidity_update"
        assert msg["symbol"] == "AAPL"
        assert "liquidity_score" in msg
        assert "synthetic_book" in msg
        assert msg["data_type"] == "synthetic_estimate"
        assert msg["is_synthetic"] is True
