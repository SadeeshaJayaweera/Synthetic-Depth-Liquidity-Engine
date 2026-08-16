"""Live Ingestor for Massive.com WebSocket tick streams.

Subscribes to trades (T.*) and quotes (Q.*) for specified symbols,
buffers incoming ticks in thread-safe queues, flushes to DuckDB in batches,
and manages automatic reconnections with exponential backoff.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set
import pandas as pd
import websockets

from synthetic_depth.config import get_settings
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

logger = logging.getLogger(__name__)


class LiveIngestor:
    """Streams live trade and quote events from Massive WebSocket into DuckDB."""

    WS_REALTIME_URL = "wss://socket.massive.com/stocks"
    WS_DELAYED_URL = "wss://delayed.massive.com/stocks"

    def __init__(
        self,
        symbols: List[str],
        api_key: Optional[str] = None,
        storage: Optional[DuckDBStorage] = None,
        delayed: bool = False,
        batch_size: int = 100,
        flush_interval_seconds: float = 1.5,
        max_reconnect_delay: float = 30.0,
    ):
        """Initialize LiveIngestor.

        Args:
            symbols: List of stock symbols to subscribe to.
            api_key: Massive API key. If None, loaded from settings/.env.
            storage: DuckDBStorage manager instance.
            delayed: Whether to connect to 15-minute delayed stream.
            batch_size: Number of messages triggering an immediate database flush.
            flush_interval_seconds: Maximum seconds between database flushes.
            max_reconnect_delay: Maximum reconnect backoff time in seconds.
        """
        settings = get_settings()
        self.api_key = api_key or settings.massive_api_key
        if not self.api_key or not self.api_key.strip():
            raise ValueError("Massive API key not configured. Set MASSIVE_API_KEY in .env.")

        self.symbols = [s.upper().strip() for s in symbols if s.strip()]
        if not self.symbols:
            raise ValueError("Must provide at least one symbol for live ingestion.")

        self.storage = storage or DuckDBStorage()
        self.delayed = delayed
        self.batch_size = batch_size
        self.flush_interval = flush_interval_seconds
        self.max_reconnect_delay = max_reconnect_delay

        self._running = False
        self._trades_buffer: List[Dict[str, Any]] = []
        self._quotes_buffer: List[Dict[str, Any]] = []
        self._last_flush_time = time.time()

        self._total_trades_received = 0
        self._total_quotes_received = 0

    @property
    def ws_url(self) -> str:
        """WebSocket endpoint URL based on delay setting."""
        return self.WS_DELAYED_URL if self.delayed else self.WS_REALTIME_URL

    def _normalize_ws_trade(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize WebSocket trade event payload."""
        sym = raw.get("sym", "").upper()
        # WebSocket timestamp is in milliseconds
        ts_ms = raw.get("t") or raw.get("pt") or int(time.time() * 1000)
        timestamp = pd.to_datetime(ts_ms, unit="ms", errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.Timestamp.utcnow()

        seq = raw.get("q", 0)
        trade_id = str(raw.get("i")) if raw.get("i") is not None else f"{sym}_{ts_ms}_{seq}"
        conditions = raw.get("c")
        cond_str = ",".join(str(c) for c in conditions) if isinstance(conditions, list) else (str(conditions) if conditions else "")

        return {
            "symbol": sym,
            "timestamp": timestamp,
            "price": float(raw.get("p", 0.0)),
            "size": int(raw.get("s", 0)),
            "exchange": str(raw.get("x", "")),
            "conditions": cond_str,
            "trade_id": trade_id,
        }

    def _normalize_ws_quote(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize WebSocket quote event payload."""
        sym = raw.get("sym", "").upper()
        ts_ms = raw.get("t") or int(time.time() * 1000)
        timestamp = pd.to_datetime(ts_ms, unit="ms", errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.Timestamp.utcnow()

        seq = raw.get("q", 0)
        quote_id = f"{sym}_{ts_ms}_{seq}"

        bx = raw.get("bx")
        ax = raw.get("ax")
        exchange_str = f"{bx}/{ax}" if bx is not None and ax is not None else str(bx or ax or "")

        return {
            "symbol": sym,
            "timestamp": timestamp,
            "bid_price": float(raw.get("bp", 0.0)),
            "bid_size": int(raw.get("bs", 0)),
            "ask_price": float(raw.get("ap", 0.0)),
            "ask_size": int(raw.get("as", 0)),
            "exchange": exchange_str,
            "quote_id": quote_id,
        }

    def flush_buffers(self) -> Dict[str, int]:
        """Flush accumulated trade and quote buffers into DuckDB."""
        flushed_trades = 0
        flushed_quotes = 0

        if self._trades_buffer:
            batch = self._trades_buffer
            self._trades_buffer = []
            flushed_trades = self.storage.insert_trades_batch(batch)
            logger.debug(f"Flushed {flushed_trades} live trades to DuckDB")

        if self._quotes_buffer:
            batch = self._quotes_buffer
            self._quotes_buffer = []
            flushed_quotes = self.storage.insert_quotes_batch(batch)
            logger.debug(f"Flushed {flushed_quotes} live quotes to DuckDB")

        self._last_flush_time = time.time()
        return {"trades": flushed_trades, "quotes": flushed_quotes}

    def process_message(self, message_text: str) -> None:
        """Process incoming raw WebSocket JSON payload."""
        try:
            items = json.loads(message_text)
            if isinstance(items, dict):
                items = [items]

            for item in items:
                ev = item.get("ev")
                if ev == "T":
                    record = self._normalize_ws_trade(item)
                    self._trades_buffer.append(record)
                    self._total_trades_received += 1
                elif ev == "Q":
                    record = self._normalize_ws_quote(item)
                    self._quotes_buffer.append(record)
                    self._total_quotes_received += 1
                elif ev == "status":
                    logger.info(f"WebSocket Status: {item.get('message', '')} ({item.get('status', '')})")

            # Check if threshold reached for buffer flush
            now = time.time()
            if (
                len(self._trades_buffer) >= self.batch_size
                or len(self._quotes_buffer) >= self.batch_size
                or (now - self._last_flush_time) >= self.flush_interval
            ):
                self.flush_buffers()

        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}", exc_info=True)

    async def _listen_loop(self) -> None:
        """Async connection and message listener loop with exponential backoff."""
        reconnect_delay = 1.0
        topics = [f"T.{s}" for s in self.symbols] + [f"Q.{s}" for s in self.symbols]
        sub_string = ",".join(topics)

        while self._running:
            try:
                logger.info(f"Connecting to Massive WebSocket at {self.ws_url}...")
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    # 1. Authenticate
                    auth_msg = json.dumps({"action": "auth", "params": self.api_key})
                    await ws.send(auth_msg)
                    logger.debug("Sent WebSocket auth handshake")

                    # 2. Subscribe to topics
                    sub_msg = json.dumps({"action": "subscribe", "params": sub_string})
                    await ws.send(sub_msg)
                    logger.info(f"Subscribed to: {sub_string}")

                    # Reset reconnect delay on successful connection
                    reconnect_delay = 1.0

                    while self._running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=self.flush_interval)
                            if isinstance(msg, bytes):
                                msg = msg.decode("utf-8")
                            self.process_message(msg)
                        except asyncio.TimeoutError:
                            # Periodic flush on idle stream
                            self.flush_buffers()

            except (websockets.ConnectionClosed, websockets.WebSocketException, OSError) as e:
                if not self._running:
                    break
                logger.warning(f"WebSocket disconnected ({e}). Reconnecting in {reconnect_delay:.1f}s...")
                self.flush_buffers()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2.0, self.max_reconnect_delay)

            except Exception as e:
                if not self._running:
                    break
                logger.error(f"Unexpected WebSocket error: {e}. Retrying in {reconnect_delay:.1f}s...", exc_info=True)
                self.flush_buffers()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2.0, self.max_reconnect_delay)

        self.flush_buffers()
        logger.info("LiveIngestor listener loop exited.")

    def run(self) -> None:
        """Start synchronous blocking live ingestion loop."""
        self._running = True
        logger.info(f"Starting LiveIngestor for symbols={self.symbols}")
        try:
            asyncio.run(self._listen_loop())
        except KeyboardInterrupt:
            logger.info("Live ingestion interrupted by user")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop ingestion loop and flush remaining buffered ticks."""
        self._running = False
        self.flush_buffers()
        logger.info(
            f"LiveIngestor stopped. Total received: trades={self._total_trades_received}, "
            f"quotes={self._total_quotes_received}"
        )
