"""Slippage and execution cost estimation endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status

from synthetic_depth.api.schemas import ConfidenceTier, SlippageRequest, SlippageResponse
from synthetic_depth.api.security import verify_api_key
from synthetic_depth.microstructure.synthetic_book import (
    SlippageEstimator,
    SyntheticDepthEstimator,
)
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

router = APIRouter(prefix="/symbols/{symbol}", tags=["Execution & Slippage"])


@router.post(
    "/slippage-estimate",
    response_model=SlippageResponse,
    summary="Estimate Order Slippage & Execution Cost",
    description=(
        "Simulate order execution by walking the reconstructed synthetic order book depth. "
        "Returns estimated average execution price (VWAP), total slippage in USD and basis points (bps), "
        "cost breakdown (half-spread vs synthetic impact), and confidence tier with operational warning flags."
    ),
)
def estimate_slippage(
    symbol: str,
    request: SlippageRequest,
    api_key: str = Depends(verify_api_key),
) -> SlippageResponse:
    """Compute execution slippage by walking the synthetic depth curve."""
    sym = symbol.upper().strip()
    storage = DuckDBStorage()

    depth_estimator = SyntheticDepthEstimator(storage=storage)
    book = depth_estimator.get_latest_synthetic_book(symbol=sym, num_levels=25)

    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No quote data available for symbol '{sym}'. Ingest data first.",
        )

    slippage_estimator = SlippageEstimator()
    estimate = slippage_estimator.estimate_slippage(
        synthetic_book=book,
        order_size=request.size,
        side=request.direction.value,
    )

    return SlippageResponse(
        symbol=estimate.symbol,
        direction=request.direction,
        order_size=estimate.order_size,
        midpoint_price=estimate.midpoint_price,
        average_execution_price=estimate.average_execution_price,
        slippage_dollars=estimate.slippage_dollars,
        slippage_bps=estimate.slippage_bps,
        half_spread_cost_bps=estimate.half_spread_cost_bps,
        impact_cost_bps=estimate.impact_cost_bps,
        levels_swept=estimate.levels_swept,
        unfilled_shares=estimate.unfilled_shares,
        confidence_score=estimate.confidence_score,
        confidence_level=ConfidenceTier(estimate.confidence_level.value),
        warnings=estimate.warnings,
        data_type=estimate.data_type,
        is_synthetic=estimate.is_synthetic,
        disclaimer=estimate.disclaimer,
    )
