"""Synthetic depth reconstruction endpoint."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from synthetic_depth.api.schemas import SyntheticBookLevelSchema, SyntheticDepthResponse
from synthetic_depth.api.security import verify_api_key
from synthetic_depth.microstructure.synthetic_book import SyntheticDepthEstimator
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

router = APIRouter(prefix="/symbols/{symbol}", tags=["Synthetic Depth"])


@router.get(
    "/synthetic-depth",
    response_model=SyntheticDepthResponse,
    summary="Reconstructed Synthetic Order Book Depth",
    description=(
        "Retrieve the reconstructed multi-level synthetic order book depth curve for a symbol. "
        "Level 1 represents the ground-truth top-of-book NBBO quote. "
        "Levels 2..N are mathematically modeled via inverted Kyle's Lambda and quote replenishment dynamics. "
        "Every response is explicitly flagged with 'data_type=synthetic_estimate' and a disclaimer."
    ),
)
def get_synthetic_depth(
    symbol: str,
    at: Optional[str] = Query(None, description="Optional target ISO timestamp (e.g. '2026-07-06 15:00:00')"),
    levels: int = Query(10, ge=1, le=50, description="Number of synthetic depth levels to generate per side"),
    tick_size: float = Query(0.01, ge=0.0001, le=1.0, description="Price step increment per level in USD"),
    api_key: str = Depends(verify_api_key),
) -> SyntheticDepthResponse:
    """Reconstruct synthetic order book depth."""
    sym = symbol.upper().strip()
    storage = DuckDBStorage()

    # Query quote at or before 'at'
    if at:
        q_sql = "SELECT * FROM quotes WHERE symbol = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1"
        q_params = [sym, at]
    else:
        q_sql = "SELECT * FROM quotes WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1"
        q_params = [sym]

    quotes_df = storage.query(q_sql, q_params)
    if quotes_df.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No quotes available for symbol '{sym}' (at={at}). Ingest data first.",
        )

    q = quotes_df.iloc[0]

    # Query latest metrics
    metrics_sql = "SELECT * FROM liquidity_metrics WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1"
    metrics_df = storage.query(metrics_sql, [sym])
    kyle_lambda = 0.00005
    replenish_ms = 500.0
    if not metrics_df.empty:
        m = metrics_df.iloc[0]
        if m["kyle_lambda"] and m["kyle_lambda"] > 0:
            kyle_lambda = float(m["kyle_lambda"])
        if m["replenishment_speed_ms"] and m["replenishment_speed_ms"] > 0:
            replenish_ms = float(m["replenishment_speed_ms"])

    estimator = SyntheticDepthEstimator(storage=storage)
    book = estimator.build_synthetic_book(
        symbol=sym,
        bid_price=float(q["bid_price"]),
        ask_price=float(q["ask_price"]),
        bid_size=int(q["bid_size"]),
        ask_size=int(q["ask_size"]),
        timestamp=q["timestamp"],
        kyle_lambda=kyle_lambda,
        replenishment_speed_ms=replenish_ms,
        num_levels=levels,
        tick_size=tick_size,
    )

    return SyntheticDepthResponse(
        symbol=book.symbol,
        timestamp=book.timestamp,
        bid_price=book.bid_price,
        ask_price=book.ask_price,
        mid_price=book.mid_price,
        spread=book.spread,
        bids=[
            SyntheticBookLevelSchema(
                level=b.level,
                price=b.price,
                size=b.size,
                cumulative_size=b.cumulative_size,
                is_synthetic=b.is_synthetic,
            )
            for b in book.bids
        ],
        asks=[
            SyntheticBookLevelSchema(
                level=a.level,
                price=a.price,
                size=a.size,
                cumulative_size=a.cumulative_size,
                is_synthetic=a.is_synthetic,
            )
            for a in book.asks
        ],
        kyle_lambda=book.kyle_lambda,
        replenishment_speed_ms=book.replenishment_speed_ms,
        data_type=book.data_type,
        is_synthetic=book.is_synthetic,
        disclaimer=book.disclaimer,
    )
