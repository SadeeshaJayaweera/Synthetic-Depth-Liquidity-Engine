"""Liquidity metrics and score endpoints."""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from synthetic_depth.api.schemas import (
    LiquidityScoreComponentBreakdown,
    LiquidityScoreResponse,
    MetricsHistoryItem,
    MetricsHistoryResponse,
)
from synthetic_depth.api.security import verify_api_key
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

router = APIRouter(prefix="/symbols/{symbol}", tags=["Liquidity Analytics"])


@router.get(
    "/liquidity-score",
    response_model=LiquidityScoreResponse,
    summary="Composite LiquidityScore & Breakdown",
    description="Retrieve the 0-100 normalized LiquidityScore and individual microstructure component breakdown for a symbol at a given timestamp (or latest available).",
)
def get_liquidity_score(
    symbol: str,
    at: Optional[str] = Query(None, description="Optional target ISO timestamp or date (e.g. '2026-07-06 14:30:00')"),
    api_key: str = Depends(verify_api_key),
) -> LiquidityScoreResponse:
    """Retrieve composite LiquidityScore and components."""
    sym = symbol.upper().strip()
    storage = DuckDBStorage()

    if at:
        query = (
            "SELECT * FROM liquidity_metrics WHERE symbol = ? AND timestamp <= ? "
            "ORDER BY timestamp DESC LIMIT 1"
        )
        params = [sym, at]
    else:
        query = "SELECT * FROM liquidity_metrics WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1"
        params = [sym]

    df = storage.query(query, params)
    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No liquidity metrics found for symbol '{sym}' (at={at}). Run metrics calculation first.",
        )

    row = df.iloc[0]
    return LiquidityScoreResponse(
        symbol=sym,
        timestamp=row["timestamp"],
        liquidity_score=float(row["liquidity_score"]),
        components=LiquidityScoreComponentBreakdown(
            kyle_lambda=float(row["kyle_lambda"]),
            amihud_ratio=float(row["amihud_ratio"]),
            replenishment_speed_ms=float(row["replenishment_speed_ms"]),
            effective_spread=float(row["effective_spread"]),
            realized_spread=float(row["realized_spread"]),
            vpin=float(row["vpin"]),
        ),
    )


@router.get(
    "/metrics/history",
    response_model=MetricsHistoryResponse,
    summary="Historical Microstructure Metrics Time Series",
    description="Retrieve a historical time series of liquidity metrics for charting and quantitative analysis.",
)
def get_metrics_history(
    symbol: str,
    start: Optional[str] = Query(None, description="Start timestamp (ISO format, e.g. '2026-07-06 09:30:00')"),
    end: Optional[str] = Query(None, description="End timestamp (ISO format, e.g. '2026-07-06 16:00:00')"),
    interval: str = Query("1min", description="Time interval identifier"),
    limit: int = Query(500, ge=1, le=5000, description="Max number of time series rows to return"),
    api_key: str = Depends(verify_api_key),
) -> MetricsHistoryResponse:
    """Retrieve historical time series of liquidity metrics."""
    sym = symbol.upper().strip()
    storage = DuckDBStorage()

    conditions = ["symbol = ?"]
    params = [sym]

    if start:
        conditions.append("timestamp >= ?")
        params.append(start)
    if end:
        conditions.append("timestamp <= ?")
        params.append(end)

    where_clause = " AND ".join(conditions)
    query = f"SELECT * FROM liquidity_metrics WHERE {where_clause} ORDER BY timestamp ASC LIMIT {limit}"

    df = storage.query(query, params)
    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No historical metrics found for symbol '{sym}' within the specified time range.",
        )

    items = [
        MetricsHistoryItem(
            timestamp=r["timestamp"],
            kyle_lambda=float(r["kyle_lambda"]),
            amihud_ratio=float(r["amihud_ratio"]),
            replenishment_speed_ms=float(r["replenishment_speed_ms"]),
            effective_spread=float(r["effective_spread"]),
            realized_spread=float(r["realized_spread"]),
            vpin=float(r["vpin"]),
            liquidity_score=float(r["liquidity_score"]),
        )
        for _, r in df.iterrows()
    ]

    return MetricsHistoryResponse(
        symbol=sym,
        interval=interval,
        total_records=len(items),
        data=items,
    )
