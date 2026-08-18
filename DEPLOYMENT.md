# Synthetic Depth Engine - Production Deployment Guide

This document provides complete instructions for deploying, configuring, scaling, and operating the **Synthetic Depth Engine** in production environments.

---

## 1. Environment Configuration

Create a `.env` file in the root directory (or inject variables via your orchestrator / Kubernetes Secrets / AWS ECS task definition):

```bash
# -----------------------------------------------------------------------------
# MASSIVE.COM (POLYGON.IO) MARKET DATA CREDENTIALS
# -----------------------------------------------------------------------------
MASSIVE_API_KEY=your_actual_massive_api_key_here

# -----------------------------------------------------------------------------
# STORAGE & DATABASE CONFIGURATION
# -----------------------------------------------------------------------------
# Local DuckDB database file path. In Docker, point to /app/data volume.
DUCKDB_PATH=/app/data/synthetic_depth.duckdb

# -----------------------------------------------------------------------------
# API SECURITY & RATE LIMITING
# -----------------------------------------------------------------------------
# Comma-separated list of allowed client API keys
API_KEYS=dev-test-key-123,client-institutional-key-xyz

# Max allowed requests per minute per API key
RATE_LIMIT_PER_MINUTE=120

# -----------------------------------------------------------------------------
# BACKGROUND WORKER & TRACKED SYMBOLS
# -----------------------------------------------------------------------------
ACTIVE_SYMBOLS=AAPL,MSFT,NVDA,SPY,QQQ

# -----------------------------------------------------------------------------
# LOGGING & OBSERVABILITY
# -----------------------------------------------------------------------------
LOG_LEVEL=INFO
ENVIRONMENT=production
```

---

## 2. Docker & Docker Compose Deployment

The project includes a multi-service `docker-compose.yml` orchestrating three dedicated containers sharing a high-performance persistent DuckDB volume:

1. **`api`** (`synthetic_depth_api`): Exposes REST & WebSocket endpoints on port `8000`.
2. **`dashboard`** (`synthetic_depth_dashboard`): Exposes the Plotly Dash Institutional Terminal on port `8050`.
3. **`worker`** (`synthetic_depth_worker`): Runs scheduled background metrics calculation (every 60s) and nightly gap-fill backfilling (at 01:00 UTC).

### Step-by-Step Commands

```bash
# 1. Clone repository and navigate to root
cd synthetic-depth-engine

# 2. Configure environment
cp .env.example .env
# Edit .env with your real MASSIVE_API_KEY

# 3. Build and launch all services in background
docker compose up -d --build

# 4. View container status
docker compose ps

# 5. Check real-time logs
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f dashboard

# 6. Verify health
curl -f http://localhost:8000/health
```

### Stopping or Restarting
```bash
# Stop containers gracefully
docker compose down

# Restart background worker
docker compose restart worker
```

---

## 3. Scaling Ingestion & Massive.com API Tier Guidance

Massive.com enforces strict tiered rate limits and WebSocket subscription quotas. Understand your plan limits before expanding symbol universes:

| Massive Tier | REST Rate Limit | Historical Window | WebSocket Streams | Recommended Symbols Universe | Ingestion Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Free Tier** | 5 requests/minute | 2 years (End of Day only) | Delayed only (1 symbol) | 1–2 symbols (Test only) | Sequential historical backfill with `rate_limit_delay=12.0` |
| **Starter ($29/mo)**| Unlimited requests/min | 5 years tick history | Real-time (Up to 5 symbols) | 5–10 active equities | Live WebSocket + periodic 1-min metrics worker |
| **Developer ($79/mo)**| Unlimited requests/min | 10+ years tick history | Real-time (Up to 50 symbols) | 25–50 active equities | Multi-symbol WebSocket + parallel backfill workers |
| **Advanced / Enterprise** | Dedicated cluster | Complete historical ticks | Full SIP feed (All US equities) | S&P 500 / NASDAQ 100 universe | Dedicated ingestor pool + Redis queue for metrics workers |

### Scaling Best Practices:
- **Rate-Limit Throttling**: On Free/Starter plans, always pass `--rate-limit-delay` (e.g. `0.2s` on Starter, `12.0s` on Free) to prevent HTTP 429 penalties.
- **DuckDB Concurrency**: DuckDB supports multiple concurrent readers alongside a single writer. The `DuckDBStorage` layer includes automatic exponential backoff retries (`with_duckdb_retry`) to prevent lock contention between the API and Background Worker.
- **Multi-Instance Deployments**: When scaling the API beyond a single machine, point DuckDB to a shared network NVMe block device or migrate to PostgreSQL / ClickHouse for multi-node writes.

---

## 4. Observability & Prometheus Monitoring

The FastAPI service exports native Prometheus telemetry at `GET /metrics` (port `8000`):

- `synthetic_depth_http_requests_total`: Request counts labeled by `method`, `endpoint`, `status_code`.
- `synthetic_depth_http_request_duration_seconds`: Histogram of request latency.
- `synthetic_depth_ingested_trades_total`: Count of ingested trades per symbol.
- `synthetic_depth_ingested_quotes_total`: Count of ingested quotes per symbol.
- `synthetic_depth_ingestion_lag_seconds`: Seconds elapsed since latest tick was recorded.
- `synthetic_depth_liquidity_score`: Latest composite LiquidityScore per symbol.

Example Prometheus scrape config:
```yaml
scrape_configs:
  - job_name: 'synthetic-depth-engine'
    scrape_interval: 15s
    static_configs:
      - targets: ['api:8000']
```

---

## 5. Quantitative Model Limitations & Operational Boundaries

> [!IMPORTANT]
> **CRITICAL QUANTITATIVE DISCLAIMER**:
> The order book depth curves and slippage estimates produced by `synthetic-depth-engine` are **mathematical statistical approximations**, NOT direct exchange Level 2 or Level 3 limit order book feeds.

### Transparent Boundary Conditions & Failure Modes

1. **Non-Displayed & Dark Pool Liquidity**:
   - The model reconstructs an idealized lit depth curve from NBBO top-of-book quotes. It **cannot observe** hidden orders, iceberg orders, or midpoint crosses in Alternative Trading Systems (ATS / Dark Pools).
2. **Exogenous News & Earnings Announcements**:
   - During scheduled corporate earnings, Fed rate decisions, or unexpected macroeconomic shocks, liquidity replenishes nonlinearly. Trailing Kyle's Lambda estimates may temporarily underestimate the severity of book thinning during sudden regime shifts.
3. **Thin & Micro-Cap Equities**:
   - In illiquid micro-caps or penny stocks where quotes update infrequently, synthetic depth estimation error increases. In these regimes, the model automatically downgrades its confidence score to `LOW` or `VERY_LOW` and raises operational warnings.
4. **Not for Deterministic Fill Guarantees**:
   - Slippage estimates are designed for Transaction Cost Analysis (TCA), algorithmic pre-trade scheduling, and portfolio risk management. They should never be treated as guaranteed execution quotes.
