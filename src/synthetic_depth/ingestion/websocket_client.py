"""Massive.com WebSocket Client Wrapper for Real-Time Streaming."""

from typing import Any, Callable, List, Optional
import logging
from massive import WebSocketClient

from synthetic_depth.config import get_settings

logger = logging.getLogger(__name__)


class MassiveWebSocketClient:
    """Wrapper around the official Massive Python WebSocketClient for real-time tick data."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        delayed: bool = False,
        subscriptions: Optional[List[str]] = None,
    ):
        """Initialize the WebSocket client.

        Args:
            api_key: Optional API key. If omitted, loaded from settings/.env.
            delayed: Whether to connect to 15-minute delayed stream. Defaults to False (realtime).
            subscriptions: Initial list of topic patterns to subscribe to (e.g. ["T.AAPL", "Q.AAPL"]).
        """
        settings = get_settings()
        self.api_key = api_key or settings.massive_api_key
        if not self.api_key or not self.api_key.strip():
            raise ValueError(
                "Massive API key not configured. Set MASSIVE_API_KEY in your .env file "
                "or pass it directly to MassiveWebSocketClient."
            )
        self.delayed = delayed
        self.subscriptions = subscriptions or []
        self._client: Optional[WebSocketClient] = None

    def connect_and_listen(self, message_handler: Callable[[List[Any]], None]) -> None:
        """Connect to Massive WebSocket cluster and process incoming messages.

        Args:
            message_handler: Callback function that receives batches of parsed WebSocket messages.
        """
        logger.info(
            f"Connecting to Massive WebSocket (delayed={self.delayed}) with subscriptions={self.subscriptions}"
        )
        self._client = WebSocketClient(
            api_key=self.api_key,
            subscriptions=self.subscriptions,
        )
        self._client.run(handle_msg=message_handler)

    def close(self) -> None:
        """Close WebSocket connection gracefully if connected."""
        if self._client:
            logger.info("Closing Massive WebSocket connection")
            self._client.close()
