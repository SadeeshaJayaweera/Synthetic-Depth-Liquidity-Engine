"""Massive.com REST API Client Wrapper.

Provides structured access to tick-level trades, quotes, and aggregate bars.
Implements exponential backoff on HTTP 429 rate limits and transparent pagination.
"""

from typing import Any, Dict, Iterator, List, Optional
import logging
import time
import httpx
import pandas as pd

from synthetic_depth.config import get_settings

logger = logging.getLogger(__name__)

# Check if official SDK is available
try:
    from massive import RESTClient as MassiveSDKClient
    SDK_AVAILABLE = True
except ImportError:
    try:
        from polygon import RESTClient as MassiveSDKClient  # type: ignore
        SDK_AVAILABLE = True
    except ImportError:
        MassiveSDKClient = None
        SDK_AVAILABLE = False


class MassiveRESTClient:
    """REST Client for Massive.com with rate-limit backoff and automatic pagination."""

    BASE_URL = "https://api.massive.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        force_httpx: bool = False,
        max_retries: int = 5,
        base_backoff: float = 1.0,
        request_delay: float = 0.0,
    ):
        """Initialize the REST client.

        Args:
            api_key: Optional Massive API key. If not provided, loaded from settings/.env.
            force_httpx: If True, uses direct httpx calls instead of SDK.
            max_retries: Maximum number of retries on 429 rate limit or transient errors.
            base_backoff: Initial backoff duration in seconds for exponential backoff.
            request_delay: Proactive throttle delay between requests in seconds.
        """
        settings = get_settings()
        self.api_key = api_key or settings.massive_api_key
        if not self.api_key or not self.api_key.strip():
            raise ValueError(
                "Massive API key not configured. Set MASSIVE_API_KEY in your .env file "
                "or pass it directly to MassiveRESTClient."
            )

        self.force_httpx = force_httpx
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.request_delay = request_delay
        self._sdk_client: Optional[Any] = None

        if SDK_AVAILABLE and not force_httpx:
            try:
                self._sdk_client = MassiveSDKClient(api_key=self.api_key)
                logger.debug("Initialized MassiveRESTClient using official SDK")
            except Exception as e:
                logger.warning(f"Failed to initialize SDK client, falling back to httpx: {e}")
                self._sdk_client = None
        else:
            logger.debug("Initialized MassiveRESTClient using direct httpx client")

    @property
    def is_using_sdk(self) -> bool:
        """Return True if using official SDK, False if using direct httpx."""
        return self._sdk_client is not None

    def _execute_http_get_with_backoff(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute HTTP GET with exponential backoff on HTTP 429 and network errors."""
        default_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "synthetic-depth-engine/0.1.0",
        }
        if headers:
            default_headers.update(headers)

        if self.request_delay > 0:
            time.sleep(self.request_delay)

        attempt = 0
        backoff = self.base_backoff

        while attempt <= self.max_retries:
            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.get(url, headers=default_headers, params=params)

                    if response.status_code == 200:
                        return response.json()

                    if response.status_code == 429:
                        attempt += 1
                        retry_after = response.headers.get("Retry-After")
                        sleep_time = float(retry_after) if retry_after else backoff
                        logger.warning(
                            f"Rate limit (429) hit on {url}. Retrying in {sleep_time:.2f}s "
                            f"(attempt {attempt}/{self.max_retries})..."
                        )
                        time.sleep(sleep_time)
                        backoff *= 2.0
                        continue

                    if 500 <= response.status_code < 600:
                        attempt += 1
                        logger.warning(
                            f"Server error ({response.status_code}) on {url}. Retrying in {backoff:.2f}s "
                            f"(attempt {attempt}/{self.max_retries})..."
                        )
                        time.sleep(backoff)
                        backoff *= 2.0
                        continue

                    error_msg = f"HTTP {response.status_code} from Massive API ({url}): {response.text}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)

            except (httpx.RequestError, httpx.TimeoutException) as e:
                attempt += 1
                if attempt > self.max_retries:
                    raise RuntimeError(f"Max retries exceeded for {url}: {e}") from e
                logger.warning(
                    f"Network error ({e}) requesting {url}. Retrying in {backoff:.2f}s "
                    f"(attempt {attempt}/{self.max_retries})..."
                )
                time.sleep(backoff)
                backoff *= 2.0

        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries} attempts.")

    def get_previous_close_agg(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch the previous day's aggregate bar for a ticker."""
        ticker = ticker.upper().strip()
        logger.info(f"Fetching previous day aggregate for ticker={ticker} (sdk={self.is_using_sdk})")

        if self.is_using_sdk:
            results = self._sdk_client.get_previous_close_agg(ticker=ticker)
            formatted = []
            for r in results:
                if hasattr(r, "__dict__"):
                    formatted.append(vars(r))
                elif isinstance(r, dict):
                    formatted.append(r)
                else:
                    formatted.append(dict(r))
            return formatted

        url = f"{self.BASE_URL}/v2/aggs/ticker/{ticker}/prev"
        data = self._execute_http_get_with_backoff(url, params={"adjusted": "true"})
        return data.get("results", [])

    def fetch_trades_page(
        self,
        ticker: str,
        timestamp: Optional[str] = None,
        timestamp_gte: Optional[str] = None,
        timestamp_lte: Optional[str] = None,
        cursor_url: Optional[str] = None,
        limit: int = 50000,
    ) -> Dict[str, Any]:
        """Fetch a single page of historical trades with cursor metadata."""
        ticker = ticker.upper().strip()
        if cursor_url:
            return self._execute_http_get_with_backoff(cursor_url)

        url = f"{self.BASE_URL}/v3/trades/{ticker}"
        params: Dict[str, Any] = {"limit": limit}
        if timestamp:
            params["timestamp"] = timestamp
        if timestamp_gte:
            params["timestamp.gte"] = timestamp_gte
        if timestamp_lte:
            params["timestamp.lte"] = timestamp_lte

        return self._execute_http_get_with_backoff(url, params=params)

    def fetch_quotes_page(
        self,
        ticker: str,
        timestamp: Optional[str] = None,
        timestamp_gte: Optional[str] = None,
        timestamp_lte: Optional[str] = None,
        cursor_url: Optional[str] = None,
        limit: int = 50000,
    ) -> Dict[str, Any]:
        """Fetch a single page of historical quotes with cursor metadata."""
        ticker = ticker.upper().strip()
        if cursor_url:
            return self._execute_http_get_with_backoff(cursor_url)

        url = f"{self.BASE_URL}/v3/quotes/{ticker}"
        params: Dict[str, Any] = {"limit": limit}
        if timestamp:
            params["timestamp"] = timestamp
        if timestamp_gte:
            params["timestamp.gte"] = timestamp_gte
        if timestamp_lte:
            params["timestamp.lte"] = timestamp_lte

        return self._execute_http_get_with_backoff(url, params=params)

    def list_trades(
        self,
        ticker: str,
        timestamp: Optional[str] = None,
        timestamp_gte: Optional[str] = None,
        timestamp_lte: Optional[str] = None,
        limit: int = 50000,
    ) -> Iterator[Dict[str, Any]]:
        """Paginate through all trade ticks for a ticker within the given time range."""
        cursor: Optional[str] = None
        while True:
            data = self.fetch_trades_page(
                ticker=ticker,
                timestamp=timestamp,
                timestamp_gte=timestamp_gte,
                timestamp_lte=timestamp_lte,
                cursor_url=cursor,
                limit=limit,
            )
            results = data.get("results", [])
            for item in results:
                yield item

            cursor = data.get("next_url")
            if not cursor or not results:
                break

    def list_quotes(
        self,
        ticker: str,
        timestamp: Optional[str] = None,
        timestamp_gte: Optional[str] = None,
        timestamp_lte: Optional[str] = None,
        limit: int = 50000,
    ) -> Iterator[Dict[str, Any]]:
        """Paginate through all quote ticks for a ticker within the given time range."""
        cursor: Optional[str] = None
        while True:
            data = self.fetch_quotes_page(
                ticker=ticker,
                timestamp=timestamp,
                timestamp_gte=timestamp_gte,
                timestamp_lte=timestamp_lte,
                cursor_url=cursor,
                limit=limit,
            )
            results = data.get("results", [])
            for item in results:
                yield item

            cursor = data.get("next_url")
            if not cursor or not results:
                break
