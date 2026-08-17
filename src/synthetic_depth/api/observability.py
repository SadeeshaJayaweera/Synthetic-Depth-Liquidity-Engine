"""Prometheus metrics telemetry and HTTP latency middleware for Synthetic Depth Engine."""

import time
from fastapi import APIRouter, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

# Prometheus Metrics Definitions
HTTP_REQUESTS_TOTAL = Counter(
    "synthetic_depth_http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "synthetic_depth_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

INGESTED_TRADES_TOTAL = Counter(
    "synthetic_depth_ingested_trades_total",
    "Total ingested trade ticks",
    ["symbol"],
)

INGESTED_QUOTES_TOTAL = Counter(
    "synthetic_depth_ingested_quotes_total",
    "Total ingested quote ticks",
    ["symbol"],
)

INGESTION_LAG_SECONDS = Gauge(
    "synthetic_depth_ingestion_lag_seconds",
    "Lag in seconds from most recent tick timestamp to UTC now",
    ["symbol"],
)

LIQUIDITY_SCORE_GAUGE = Gauge(
    "synthetic_depth_liquidity_score",
    "Current normalized LiquidityScore (0-100)",
    ["symbol"],
)


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Starlette middleware to measure request counts and latency per endpoint."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        endpoint = request.url.path

        # Normalize parameterized paths to avoid metric cardinality explosion
        normalized_path = endpoint
        parts = endpoint.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "symbols":
            parts[1] = "{symbol}"
            normalized_path = "/" + "/".join(parts)

        try:
            response = await call_next(request)
            status_code = str(response.status_code)
        except Exception:
            status_code = "500"
            raise
        finally:
            duration = time.time() - start_time
            if not endpoint.startswith("/metrics"):
                HTTP_REQUESTS_TOTAL.labels(
                    method=request.method,
                    endpoint=normalized_path,
                    status_code=status_code,
                ).inc()
                HTTP_REQUEST_DURATION_SECONDS.labels(
                    method=request.method,
                    endpoint=normalized_path,
                ).observe(duration)

        return response


router = APIRouter(tags=["Observability"])


@router.get(
    "/metrics",
    summary="Prometheus Metrics",
    description="Exposes application telemetry, request counts, latencies, and microstructure indicators in Prometheus text format.",
)
def get_prometheus_metrics():
    """Return metrics in Prometheus exposition format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
