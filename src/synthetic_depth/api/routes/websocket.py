"""Live streaming WebSocket endpoint for liquidity score and synthetic depth."""

import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from synthetic_depth.config import get_settings
from synthetic_depth.microstructure.synthetic_book import SyntheticDepthEstimator
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Live Streaming"])


@router.websocket("/ws/symbols/{symbol}/live-liquidity")
async def live_liquidity_stream(
    websocket: WebSocket,
    symbol: str,
    api_key: Optional[str] = Query(None),
):
    """Stream live liquidity score and synthetic order book depth snapshots via WebSocket."""
    settings = get_settings()
    # Simple check for query param auth if provided
    if api_key and api_key not in settings.api_keys:
        await websocket.close(code=1008, reason="Unauthorized API Key")
        return

    await websocket.accept()
    sym = symbol.upper().strip()
    storage = DuckDBStorage()
    estimator = SyntheticDepthEstimator(storage=storage)

    logger.info(f"WebSocket client connected to live liquidity stream for {sym}")

    try:
        while True:
            # Query latest quote and metrics
            book = estimator.get_latest_synthetic_book(symbol=sym, num_levels=5)
            metrics_df = storage.query(
                "SELECT * FROM liquidity_metrics WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                [sym],
            )

            score_val = 50.0
            if not metrics_df.empty and "liquidity_score" in metrics_df.columns:
                score_val = float(metrics_df.iloc[0]["liquidity_score"])

            if book:
                payload = {
                    "type": "live_liquidity_update",
                    "symbol": sym,
                    "timestamp": str(book.timestamp),
                    "liquidity_score": round(score_val, 2),
                    "bid_price": book.bid_price,
                    "ask_price": book.ask_price,
                    "mid_price": book.mid_price,
                    "spread": book.spread,
                    "synthetic_book": book.to_dict(),
                    "data_type": "synthetic_estimate",
                    "is_synthetic": True,
                }
            else:
                payload = {
                    "type": "waiting_for_data",
                    "symbol": sym,
                    "message": f"Waiting for live ticks/quotes for symbol {sym}",
                }

            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected from {sym} stream")
    except Exception as e:
        logger.error(f"WebSocket error for {sym}: {e}", exc_info=True)
        try:
            await websocket.close(code=1011, reason="Internal Server Error")
        except Exception:
            pass
    finally:
        storage.close()
