"""Pydantic request and response schemas for Synthetic Depth Engine API.

Includes structural disclaimers and confidence indicators in all modeled depth responses.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

DEFAULT_DISCLAIMER = (
    "DISCLAIMER: This order book depth is a synthetic mathematical reconstruction "
    "modeled from NBBO top-of-book quotes, Kyle's lambda price impact, and quote replenishment "
    "dynamics. It does NOT represent direct Level 2 or Level 3 exchange order book feeds."
)


class OrderDirection(str, Enum):
    """Trading direction."""

    BUY = "BUY"
    SELL = "SELL"


class ConfidenceTier(str, Enum):
    """Confidence tier for synthetic model estimates."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"


class HealthResponse(BaseModel):
    """API health status and storage telemetry."""

    status: str = Field(..., description="Service health status", examples=["healthy"])
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Server timestamp (UTC)")
    duckdb_connected: bool = Field(..., description="Whether DuckDB connection is active")
    last_ingestion_timestamp: Optional[datetime] = Field(None, description="Timestamp of the most recent market tick in DuckDB")
    table_counts: Dict[str, int] = Field(..., description="Record counts for all persistent tables")


class LiquidityScoreComponentBreakdown(BaseModel):
    """Component metrics contributing to the composite LiquidityScore."""

    kyle_lambda: float = Field(..., description="Price impact coefficient ($/share)", examples=[0.000021])
    amihud_ratio: float = Field(..., description="Amihud illiquidity ratio (|return| / dollar volume)", examples=[0.0013])
    replenishment_speed_ms: float = Field(..., description="Median quote depth replenishment speed in milliseconds", examples=[500.0])
    effective_spread: float = Field(..., description="Average effective spread ($)", examples=[0.0129])
    realized_spread: float = Field(..., description="Average 5-min realized spread ($)", examples=[0.0062])
    vpin: float = Field(..., description="Volume-Synchronized Probability of Informed Trading (toxicity)", examples=[0.0292])


class LiquidityScoreResponse(BaseModel):
    """Composite liquidity health indicator and metric breakdown."""

    symbol: str = Field(..., description="Stock ticker symbol", examples=["AAPL"])
    timestamp: datetime = Field(..., description="Timestamp of the metric observation (UTC)")
    liquidity_score: float = Field(..., description="Normalized composite liquidity score (0-100, where 100 is most liquid)", examples=[85.5])
    components: LiquidityScoreComponentBreakdown = Field(..., description="Individual microstructure metric values")
    data_type: str = Field("microstructure_metrics", description="Data category indicator")


class SyntheticBookLevelSchema(BaseModel):
    """Individual synthetic price level in the reconstructed depth curve."""

    level: int = Field(..., description="Depth level index (1 = Touch/NBBO, 2..N = Modeled)", examples=[1])
    price: float = Field(..., description="Price level in USD", examples=[150.00])
    size: int = Field(..., description="Estimated shares available at this price level", examples=[500])
    cumulative_size: int = Field(..., description="Cumulative shares available up to this level", examples=[500])
    is_synthetic: bool = True


class SyntheticDepthResponse(BaseModel):
    """Reconstructed synthetic order book depth response."""

    symbol: str = Field(..., description="Stock ticker symbol", examples=["AAPL"])
    timestamp: datetime = Field(..., description="Quote snapshot timestamp (UTC)")
    bid_price: float = Field(..., description="Prevailing best bid price", examples=[150.00])
    ask_price: float = Field(..., description="Prevailing best ask price", examples=[150.02])
    mid_price: float = Field(..., description="Quote midpoint price", examples=[150.01])
    spread: float = Field(..., description="Bid-ask spread in USD", examples=[0.02])
    bids: List[SyntheticBookLevelSchema] = Field(..., description="Bids depth curve (descending prices)")
    asks: List[SyntheticBookLevelSchema] = Field(..., description="Asks depth curve (ascending prices)")
    kyle_lambda: float = Field(..., description="Historical Kyle's Lambda parameter used for curve inversion", examples=[0.00005])
    replenishment_speed_ms: float = Field(..., description="Quote replenishment speed parameter in ms", examples=[500.0])
    data_type: str = Field("synthetic_estimate", description="Structural marker indicating modeled data")
    is_synthetic: bool = Field(True, description="Always true for synthetic reconstructed order book")
    disclaimer: str = Field(DEFAULT_DISCLAIMER, description="Mandatory legal and quantitative disclaimer")


class SlippageRequest(BaseModel):
    """Order parameters for execution slippage estimation."""

    size: int = Field(..., ge=1, description="Order size in shares", examples=[2500])
    direction: OrderDirection = Field(default=OrderDirection.BUY, description="Order side ('BUY' or 'SELL')")


class SlippageResponse(BaseModel):
    """Estimated execution slippage and cost decomposition."""

    symbol: str = Field(..., description="Stock ticker symbol", examples=["AAPL"])
    direction: OrderDirection = Field(..., description="Order side evaluated")
    order_size: int = Field(..., description="Order size evaluated in shares", examples=[2500])
    midpoint_price: float = Field(..., description="Quote midpoint price at estimation time", examples=[150.01])
    average_execution_price: float = Field(..., description="Estimated Volume-Weighted Average Execution Price (VWAP)", examples=[150.04])
    slippage_dollars: float = Field(..., description="Total slippage per share in USD", examples=[0.03])
    slippage_bps: float = Field(..., description="Total slippage in basis points relative to midpoint", examples=[2.0])
    half_spread_cost_bps: float = Field(..., description="Cost to cross the NBBO touch from midpoint in basis points", examples=[0.67])
    impact_cost_bps: float = Field(..., description="Synthetic depth market impact cost beyond the touch in basis points", examples=[1.33])
    levels_swept: int = Field(..., description="Number of synthetic depth levels swept to fill the order", examples=[3])
    unfilled_shares: int = Field(..., description="Unfilled shares beyond modeled capacity (0 if fully filled)", examples=[0])
    confidence_score: float = Field(..., description="Reliability score of the estimate (0.0 to 1.0)", examples=[0.85])
    confidence_level: ConfidenceTier = Field(..., description="Confidence tier (HIGH, MEDIUM, LOW, VERY_LOW)")
    warnings: List[str] = Field(default_factory=list, description="Operational warnings or low-confidence flags")
    data_type: str = Field("synthetic_estimate", description="Structural marker indicating modeled estimate")
    is_synthetic: bool = Field(True, description="Always true for synthetic estimates")
    disclaimer: str = Field(DEFAULT_DISCLAIMER, description="Mandatory quantitative disclaimer")


class MetricsHistoryItem(BaseModel):
    """Individual historical time-series observation of liquidity metrics."""

    timestamp: datetime = Field(..., description="Time bucket timestamp (UTC)")
    kyle_lambda: float = Field(..., description="Kyle's Lambda price impact ($/share)")
    amihud_ratio: float = Field(..., description="Amihud illiquidity ratio")
    replenishment_speed_ms: float = Field(..., description="Quote replenishment speed (ms)")
    effective_spread: float = Field(..., description="Effective spread ($)")
    realized_spread: float = Field(..., description="5-minute Realized spread ($)")
    vpin: float = Field(..., description="VPIN order flow toxicity")
    liquidity_score: float = Field(..., description="Composite LiquidityScore (0-100)")


class MetricsHistoryResponse(BaseModel):
    """Historical time-series response for charting and analysis."""

    symbol: str = Field(..., description="Stock ticker symbol", examples=["AAPL"])
    interval: str = Field(..., description="Time bucket aggregation interval", examples=["1min"])
    total_records: int = Field(..., description="Number of historical time buckets returned", examples=[390])
    data: List[MetricsHistoryItem] = Field(..., description="Chronological array of metrics observations")
