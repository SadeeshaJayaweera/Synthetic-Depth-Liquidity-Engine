"""API Route handlers."""

from synthetic_depth.api.routes.health import router as health_router
from synthetic_depth.api.routes.liquidity import router as liquidity_router
from synthetic_depth.api.routes.synthetic_depth import router as synthetic_depth_router
from synthetic_depth.api.routes.slippage import router as slippage_router
from synthetic_depth.api.routes.websocket import router as websocket_router

__all__ = [
    "health_router",
    "liquidity_router",
    "synthetic_depth_router",
    "slippage_router",
    "websocket_router",
]
