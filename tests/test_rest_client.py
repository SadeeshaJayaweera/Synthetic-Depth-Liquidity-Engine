"""Tests for MassiveRESTClient including httpx fallback, pagination, and rate limit backoff."""

from unittest.mock import MagicMock, patch
import httpx
import pytest
from synthetic_depth.ingestion.rest_client import MassiveRESTClient


def test_rest_client_missing_key():
    """Client raises ValueError when no key is configured."""
    with patch("synthetic_depth.ingestion.rest_client.get_settings") as mock_settings:
        mock_instance = MagicMock()
        mock_instance.massive_api_key = None
        mock_settings.return_value = mock_instance
        with pytest.raises(ValueError, match="Massive API key not configured"):
            MassiveRESTClient()


def test_rest_client_httpx_previous_close():
    """Test get_previous_close_agg using direct httpx fallback mode."""
    client = MassiveRESTClient(api_key="test_api_key", force_httpx=True)
    assert client.is_using_sdk is False

    mock_response = {
        "status": "OK",
        "ticker": "AAPL",
        "results": [
            {
                "T": "AAPL",
                "o": 220.0,
                "h": 225.0,
                "l": 219.0,
                "c": 224.5,
                "v": 50000000,
                "vw": 223.5,
                "t": 1723838400000,
            }
        ],
    }

    with patch.object(client, "_execute_http_get_with_backoff", return_value=mock_response) as mock_get:
        results = client.get_previous_close_agg("AAPL")
        assert len(results) == 1
        assert results[0]["c"] == 224.5
        mock_get.assert_called_once_with("https://api.massive.com/v2/aggs/ticker/AAPL/prev", params={"adjusted": "true"})


def test_rest_client_rate_limit_backoff_and_retry():
    """Test 429 rate limit response triggers backoff and retry."""
    client = MassiveRESTClient(api_key="test_api_key", force_httpx=True, max_retries=3, base_backoff=0.01)

    mock_req = httpx.Request("GET", "https://api.massive.com/v2/aggs/ticker/AAPL/prev")
    resp_429 = httpx.Response(429, request=mock_req, text='{"error":"Too Many Requests"}', headers={"Retry-After": "0.01"})
    resp_200 = httpx.Response(200, request=mock_req, json={"status": "OK", "results": [{"c": 150.0}]})

    with patch("httpx.Client.get", side_effect=[resp_429, resp_200]) as mock_http:
        res = client._execute_http_get_with_backoff("https://api.massive.com/v2/aggs/ticker/AAPL/prev")
        assert res["status"] == "OK"
        assert mock_http.call_count == 2
