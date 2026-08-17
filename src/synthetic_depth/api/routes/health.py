"""Health check and telemetry endpoint."""

from datetime import datetime
from fastapi import APIRouter, Depends
from synthetic_depth.api.schemas import HealthResponse
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

router = APIRouter(tags=["System"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="System Health & Storage Telemetry",
    description="Returns the operational health of the API, DuckDB connection status, table record counts, and last ingested tick timestamp.",
)
def get_health() -> HealthResponse:
    """Retrieve system health and telemetry."""
    storage = DuckDBStorage()
    try:
        counts = storage.get_table_counts()
        duckdb_ok = True
    except Exception:
        counts = {"trades": 0, "quotes": 0, "classified_trades": 0, "liquidity_metrics": 0, "ingestion_log": 0}
        duckdb_ok = False

    # Query latest trade timestamp
    last_tick_ts = None
    if duckdb_ok:
        try:
            row = storage.query("SELECT timestamp FROM trades ORDER BY timestamp DESC LIMIT 1")
            if not row.empty:
                last_tick_ts = row.iloc[0]["timestamp"]
        except Exception:
            pass

    return HealthResponse(
        status="healthy" if duckdb_ok else "degraded",
        timestamp=datetime.utcnow(),
        duckdb_connected=duckdb_ok,
        last_ingestion_timestamp=last_tick_ts,
        table_counts=counts,
    )
