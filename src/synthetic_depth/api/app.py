"""FastAPI Application Entrypoint for Synthetic Depth Engine.

Assembles API routers, security middleware, Prometheus telemetry, and auto-generated OpenAPI documentation.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from synthetic_depth.api.observability import PrometheusMetricsMiddleware, router as metrics_router
from synthetic_depth.api.routes import (
    health_router,
    liquidity_router,
    synthetic_depth_router,
    slippage_router,
    websocket_router,
)

API_DESCRIPTION = """
## Synthetic Depth Engine API

A high-performance market-liquidity and microstructure analytics API built on top of the Massive.com (Polygon.io) stock market data feed.

### Core Capabilities:
1. **Composite LiquidityScore**: 0-100 normalized score synthesizing Kyle's Lambda price impact, Amihud illiquidity, quote replenishment speed, effective/realized spreads, and VPIN toxicity.
2. **Synthetic Order Book Depth**: Modeled depth curves beyond the NBBO top-of-book, clearly marked with structural disclaimers.
3. **Execution Slippage Estimation**: Algorithmic order-walking simulation providing VWAP execution estimates, bps slippage, and confidence levels.
4. **Historical Microstructure Time Series**: High-frequency metrics time series for charting and research backtesting.
5. **Live WebSocket Streaming**: Real-time tick updates and depth snapshots.
6. **Prometheus Telemetry**: Native `/metrics` endpoint with request counts, latency, and microstructure indicators.

### Authentication:
Pass your API key in the `X-API-Key` request header (e.g. `X-API-Key: dev-test-key-123`).
"""


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="Synthetic Depth Engine API",
        description=API_DESCRIPTION,
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Enable CORS for frontend clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register Prometheus Metrics Middleware
    app.add_middleware(PrometheusMetricsMiddleware)

    # Register API Routers
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(liquidity_router)
    app.include_router(synthetic_depth_router)
    app.include_router(slippage_router)
    app.include_router(websocket_router)

    return app


app = create_app()
