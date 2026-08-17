"""Tests for Prometheus observability telemetry and metrics endpoint."""

import pytest
from fastapi.testclient import TestClient
from synthetic_depth.api.app import app


@pytest.fixture
def client():
    """TestClient for FastAPI app."""
    return TestClient(app)


def test_prometheus_metrics_endpoint(client):
    """Test /metrics endpoint returns Prometheus formatted metrics."""
    # Hit health endpoint to generate request telemetry
    client.get("/health")

    # Fetch Prometheus metrics
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")

    body = response.text
    assert "synthetic_depth_http_requests_total" in body
    assert "synthetic_depth_http_request_duration_seconds" in body
