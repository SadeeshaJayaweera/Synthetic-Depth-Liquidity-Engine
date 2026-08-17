"""Synthetic Order Book Reconstruction & Execution Slippage Estimation Engine.

Infers synthetic multi-level order book depth beyond the top-of-book NBBO by inverting
Kyle's Lambda (price-impact coefficient) and weighting queue replenishment dynamics.

IMPORTANT:
This module produces MODELED/ESTIMATED liquidity distributions, NOT direct exchange Level 2/3 feeds.
All outputs are explicitly labeled with `data_type='synthetic_estimate'` and `is_synthetic=True`.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
import logging
import numpy as np
import pandas as pd

from synthetic_depth.storage.duckdb_layer import DuckDBStorage

logger = logging.getLogger(__name__)

SYNTHETIC_DISCLAIMER = (
    "DISCLAIMER: This order book depth is a synthetic mathematical reconstruction "
    "modeled from NBBO top-of-book quotes, Kyle's lambda price impact, and quote replenishment "
    "dynamics. It does NOT represent direct Level 2 or Level 3 exchange order book feeds."
)


class ConfidenceLevel(str, Enum):
    """Reliability tier for slippage estimation."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"


@dataclass
class SyntheticBookLevel:
    """Individual synthetic price level in the reconstructed depth curve."""

    level: int
    price: float
    size: int
    cumulative_size: int
    is_synthetic: bool = True


@dataclass
class SyntheticOrderBook:
    """Reconstructed synthetic multi-level order book."""

    symbol: str
    timestamp: Union[str, datetime]
    bid_price: float
    ask_price: float
    mid_price: float
    spread: float
    bids: List[SyntheticBookLevel] = field(default_factory=list)
    asks: List[SyntheticBookLevel] = field(default_factory=list)
    kyle_lambda: float = 0.00005
    replenishment_speed_ms: float = 500.0
    data_type: str = "synthetic_estimate"
    is_synthetic: bool = True
    disclaimer: str = SYNTHETIC_DISCLAIMER

    def to_dict(self) -> Dict[str, Any]:
        """Serialize synthetic book to dictionary."""
        return {
            "symbol": self.symbol,
            "timestamp": str(self.timestamp),
            "bid_price": round(self.bid_price, 4),
            "ask_price": round(self.ask_price, 4),
            "mid_price": round(self.mid_price, 4),
            "spread": round(self.spread, 4),
            "bids": [asdict(lvl) for lvl in self.bids],
            "asks": [asdict(lvl) for lvl in self.asks],
            "kyle_lambda": round(self.kyle_lambda, 8),
            "replenishment_speed_ms": round(self.replenishment_speed_ms, 1),
            "data_type": self.data_type,
            "is_synthetic": self.is_synthetic,
            "disclaimer": self.disclaimer,
        }


@dataclass
class SlippageEstimate:
    """Detailed slippage and execution cost breakdown for an order."""

    symbol: str
    side: str
    order_size: int
    midpoint_price: float
    average_execution_price: float
    slippage_dollars: float
    slippage_bps: float
    half_spread_cost_bps: float
    impact_cost_bps: float
    levels_swept: int
    unfilled_shares: int
    confidence_score: float
    confidence_level: ConfidenceLevel
    warnings: List[str] = field(default_factory=list)
    data_type: str = "synthetic_estimate"
    is_synthetic: bool = True
    disclaimer: str = SYNTHETIC_DISCLAIMER

    def to_dict(self) -> Dict[str, Any]:
        """Serialize slippage estimate to dictionary."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "order_size": self.order_size,
            "midpoint_price": round(self.midpoint_price, 4),
            "average_execution_price": round(self.average_execution_price, 4),
            "slippage_dollars": round(self.slippage_dollars, 4),
            "slippage_bps": round(self.slippage_bps, 2),
            "half_spread_cost_bps": round(self.half_spread_cost_bps, 2),
            "impact_cost_bps": round(self.impact_cost_bps, 2),
            "levels_swept": self.levels_swept,
            "unfilled_shares": self.unfilled_shares,
            "confidence_score": round(self.confidence_score, 2),
            "confidence_level": self.confidence_level.value,
            "warnings": self.warnings,
            "data_type": self.data_type,
            "is_synthetic": self.is_synthetic,
            "disclaimer": self.disclaimer,
        }


class SyntheticDepthEstimator:
    """Reconstructs synthetic multi-level depth curves from NBBO quotes and microstructure parameters."""

    def __init__(self, storage: Optional[DuckDBStorage] = None):
        """Initialize SyntheticDepthEstimator.

        Args:
            storage: Optional DuckDBStorage instance for historical lookup.
        """
        self.storage = storage or DuckDBStorage()

    def build_synthetic_book(
        self,
        symbol: str,
        bid_price: float,
        ask_price: float,
        bid_size: int,
        ask_size: int,
        timestamp: Optional[Union[str, datetime]] = None,
        kyle_lambda: float = 0.00005,
        replenishment_speed_ms: float = 500.0,
        num_levels: int = 10,
        tick_size: float = 0.01,
    ) -> SyntheticOrderBook:
        """Construct synthetic multi-level order book for a symbol.

        Formula:
            Level 0 (Touch): Real NBBO quote size S_0.
            Level k >= 1 (Synthetic):
                Delta P_k = k * tick_size
                Marginal Size S_k = S_0 * exp(-k / kappa) + (tick_size / lambda) * (1 + 1000 / (tau_replenish + 100))

        Args:
            symbol: Stock ticker symbol.
            bid_price: Prevailing best bid price.
            ask_price: Prevailing best ask price.
            bid_size: Shares available at best bid.
            ask_size: Shares available at best ask.
            timestamp: Quote timestamp.
            kyle_lambda: Price impact parameter ($/share).
            replenishment_speed_ms: Median recovery time in ms.
            num_levels: Number of depth levels to generate per side.
            tick_size: Price increment per level.

        Returns:
            SyntheticOrderBook containing reconstructed bids and asks.
        """
        ts = timestamp or datetime.utcnow()
        mid_price = round((bid_price + ask_price) / 2.0, 4)
        spread = round(max(0.0001, ask_price - bid_price), 4)

        # Baseline parameters
        safe_lambda = max(1e-6, kyle_lambda)
        safe_replenish = max(10.0, replenishment_speed_ms)

        # Replenishment liquidity multiplier
        replenish_multiplier = 1.0 + (500.0 / safe_replenish)

        # Base queue size derived from lambda inversion
        lambda_depth_base = (tick_size / safe_lambda) * replenish_multiplier

        # 1. Build Bids (descending prices)
        bids: List[SyntheticBookLevel] = []
        cum_bid_size = 0
        for k in range(num_levels):
            lvl_price = round(bid_price - (k * tick_size), 4)
            if lvl_price <= 0:
                break
            if k == 0:
                # Top of book is real
                lvl_size = max(10, int(bid_size))
                is_synth = False
            else:
                # Synthetic level
                decay = np.exp(-k / 4.0)
                lvl_size = int(round(bid_size * decay + lambda_depth_base * (1.0 - decay * 0.5)))
                lvl_size = max(10, lvl_size)
                is_synth = True

            cum_bid_size += lvl_size
            bids.append(SyntheticBookLevel(
                level=k + 1,
                price=lvl_price,
                size=lvl_size,
                cumulative_size=cum_bid_size,
                is_synthetic=is_synth,
            ))

        # 2. Build Asks (ascending prices)
        asks: List[SyntheticBookLevel] = []
        cum_ask_size = 0
        for k in range(num_levels):
            lvl_price = round(ask_price + (k * tick_size), 4)
            if k == 0:
                # Top of book is real
                lvl_size = max(10, int(ask_size))
                is_synth = False
            else:
                # Synthetic level
                decay = np.exp(-k / 4.0)
                lvl_size = int(round(ask_size * decay + lambda_depth_base * (1.0 - decay * 0.5)))
                lvl_size = max(10, lvl_size)
                is_synth = True

            cum_ask_size += lvl_size
            asks.append(SyntheticBookLevel(
                level=k + 1,
                price=lvl_price,
                size=lvl_size,
                cumulative_size=cum_ask_size,
                is_synthetic=is_synth,
            ))

        return SyntheticOrderBook(
            symbol=symbol.upper(),
            timestamp=ts,
            bid_price=round(bid_price, 4),
            ask_price=round(ask_price, 4),
            mid_price=mid_price,
            spread=spread,
            bids=bids,
            asks=asks,
            kyle_lambda=kyle_lambda,
            replenishment_speed_ms=replenishment_speed_ms,
        )

    def get_latest_synthetic_book(
        self,
        symbol: str,
        num_levels: int = 10,
        tick_size: float = 0.01,
    ) -> Optional[SyntheticOrderBook]:
        """Fetch latest quotes and metrics from DuckDB to build current synthetic book."""
        symbol = symbol.upper().strip()
        quotes_df = self.storage.query(
            "SELECT * FROM quotes WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            [symbol],
        )
        if quotes_df.empty:
            logger.warning(f"No quotes found in DuckDB for {symbol}")
            return None

        q = quotes_df.iloc[0]

        # Fetch latest metrics if available
        metrics_df = self.storage.query(
            "SELECT * FROM liquidity_metrics WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            [symbol],
        )
        kyle_lambda = 0.00005
        replenish_ms = 500.0
        if not metrics_df.empty:
            m = metrics_df.iloc[0]
            if pd.notna(m.get("kyle_lambda")) and m["kyle_lambda"] > 0:
                kyle_lambda = float(m["kyle_lambda"])
            if pd.notna(m.get("replenishment_speed_ms")) and m["replenishment_speed_ms"] > 0:
                replenish_ms = float(m["replenishment_speed_ms"])

        return self.build_synthetic_book(
            symbol=symbol,
            bid_price=float(q["bid_price"]),
            ask_price=float(q["ask_price"]),
            bid_size=int(q["bid_size"]),
            ask_size=int(q["ask_size"]),
            timestamp=q["timestamp"],
            kyle_lambda=kyle_lambda,
            replenishment_speed_ms=replenish_ms,
            num_levels=num_levels,
            tick_size=tick_size,
        )


class SlippageEstimator:
    """Estimates average execution price, slippage, and confidence by walking synthetic depth."""

    def estimate_slippage(
        self,
        synthetic_book: SyntheticOrderBook,
        order_size: int,
        side: str = "BUY",
    ) -> SlippageEstimate:
        """Walk the synthetic depth curve and compute execution slippage for an order.

        Args:
            synthetic_book: Reconstructed SyntheticOrderBook instance.
            order_size: Number of shares to execute (must be > 0).
            side: Order direction ('BUY' walks asks, 'SELL' walks bids).

        Returns:
            SlippageEstimate with execution price, bps slippage, cost breakdown, and confidence.
        """
        side_clean = side.upper().strip()
        if side_clean not in ["BUY", "SELL"]:
            raise ValueError(f"Invalid side '{side}'. Must be 'BUY' or 'SELL'.")

        if order_size <= 0:
            raise ValueError("Order size must be positive.")

        levels = synthetic_book.asks if side_clean == "BUY" else synthetic_book.bids
        midpoint = synthetic_book.mid_price
        touch_price = synthetic_book.ask_price if side_clean == "BUY" else synthetic_book.bid_price
        touch_size = levels[0].size if levels else 100

        remaining = order_size
        cost_accum = 0.0
        levels_swept = 0
        warnings: List[str] = []

        for lvl in levels:
            levels_swept += 1
            fill = min(remaining, lvl.size)
            cost_accum += fill * lvl.price
            remaining -= fill
            if remaining == 0:
                break

        unfilled = remaining
        filled_shares = order_size - unfilled

        if filled_shares > 0:
            avg_price = cost_accum / filled_shares
        else:
            avg_price = touch_price

        # If order size exceeds all synthetic levels, extrapolate remaining at worst price + penalty
        if unfilled > 0:
            worst_price = levels[-1].price if levels else touch_price
            penalty_per_share = (synthetic_book.spread / 2.0) + (synthetic_book.kyle_lambda * unfilled)
            if side_clean == "BUY":
                penalty_price = worst_price + penalty_per_share
            else:
                penalty_price = max(0.01, worst_price - penalty_per_share)

            cost_accum += unfilled * penalty_price
            avg_price = cost_accum / order_size
            warnings.append(
                f"Order size ({order_size:,} shs) exceeds modeled synthetic depth capacity "
                f"({order_size - unfilled:,} shs). Extrapolated remaining {unfilled:,} shs."
            )

        # Slippage calculations
        if side_clean == "BUY":
            slippage_dollars = max(0.0, avg_price - midpoint)
        else:
            slippage_dollars = max(0.0, midpoint - avg_price)

        slippage_bps = (slippage_dollars / midpoint) * 10_000.0

        # Half-spread cost (cost to cross the touch from midpoint)
        half_spread = abs(touch_price - midpoint)
        half_spread_cost_bps = (half_spread / midpoint) * 10_000.0

        # Impact cost (cost beyond the touch)
        impact_cost_bps = max(0.0, slippage_bps - half_spread_cost_bps)

        # Confidence assessment
        confidence_score, confidence_level, conf_warnings = self._calculate_confidence(
            order_size=order_size,
            touch_size=touch_size,
            levels_swept=levels_swept,
            unfilled=unfilled,
            kyle_lambda=synthetic_book.kyle_lambda,
            replenishment_speed_ms=synthetic_book.replenishment_speed_ms,
        )
        warnings.extend(conf_warnings)

        return SlippageEstimate(
            symbol=synthetic_book.symbol,
            side=side_clean,
            order_size=order_size,
            midpoint_price=midpoint,
            average_execution_price=avg_price,
            slippage_dollars=slippage_dollars,
            slippage_bps=slippage_bps,
            half_spread_cost_bps=half_spread_cost_bps,
            impact_cost_bps=impact_cost_bps,
            levels_swept=levels_swept,
            unfilled_shares=unfilled,
            confidence_score=confidence_score,
            confidence_level=confidence_level,
            warnings=warnings,
        )

    def _calculate_confidence(
        self,
        order_size: int,
        touch_size: int,
        levels_swept: int,
        unfilled: int,
        kyle_lambda: float,
        replenishment_speed_ms: float,
    ) -> Tuple[float, ConfidenceLevel, List[str]]:
        """Calculate confidence score and flags for slippage estimate."""
        score = 1.0
        warnings: List[str] = []

        # 1. Size relative to top-of-book touch
        size_ratio = order_size / max(1, touch_size)
        if size_ratio <= 1.0:
            score *= 1.0  # Fully at touch (highest confidence)
        elif size_ratio <= 5.0:
            score *= 0.85
        elif size_ratio <= 15.0:
            score *= 0.65
            warnings.append("Order size is moderately large relative to prevailing NBBO touch size.")
        else:
            score *= 0.40
            warnings.append(
                f"Oversized order ({order_size:,} shares vs {touch_size:,} at touch). "
                "Execution depends heavily on unobservable hidden queue depth."
            )

        # 2. Complete depth exhaustion
        if unfilled > 0:
            score *= 0.50
            warnings.append("Synthetic book depth was fully exhausted.")

        # 3. Microstructure parameter sanity
        if kyle_lambda <= 1e-6 or kyle_lambda > 0.01:
            score *= 0.70
            warnings.append("Kyle's lambda parameter is at extreme boundaries.")

        if replenishment_speed_ms > 4000.0:
            score *= 0.80
            warnings.append("Slow quote replenishment indicates elevated market fragility.")

        score = max(0.05, min(0.99, score))

        if score >= 0.80:
            level = ConfidenceLevel.HIGH
        elif score >= 0.60:
            level = ConfidenceLevel.MEDIUM
        elif score >= 0.35:
            level = ConfidenceLevel.LOW
        else:
            level = ConfidenceLevel.VERY_LOW

        return score, level, warnings
