"""Historical Ingestor for Massive.com REST tick data (Trades and NBBO Quotes).

Iterates through daily trading sessions, paginates via cursor, normalizes records,
and bulk-persists into DuckDB with idempotency, resume capability, and rate limiting.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Union
import logging
import pandas as pd

from synthetic_depth.ingestion.rest_client import MassiveRESTClient
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

logger = logging.getLogger(__name__)


class HistoricalIngestor:
    """Orchestrates historical tick data backfill into DuckDB."""

    def __init__(
        self,
        rest_client: Optional[MassiveRESTClient] = None,
        storage: Optional[DuckDBStorage] = None,
        batch_size: int = 25000,
    ):
        """Initialize HistoricalIngestor.

        Args:
            rest_client: Configured MassiveRESTClient instance.
            storage: Configured DuckDBStorage instance.
            batch_size: Batch flush size for database insertions.
        """
        self.client = rest_client or MassiveRESTClient()
        self.storage = storage or DuckDBStorage()
        self.batch_size = batch_size

    def _generate_date_range(self, start: Union[str, date], end: Union[str, date]) -> List[str]:
        """Generate list of date strings (YYYY-MM-DD) between start and end inclusive."""
        if isinstance(start, str):
            start_dt = datetime.strptime(start, "%Y-%m-%d").date()
        else:
            start_dt = start

        if isinstance(end, str):
            end_dt = datetime.strptime(end, "%Y-%m-%d").date()
        else:
            end_dt = end

        if start_dt > end_dt:
            raise ValueError(f"Start date {start_dt} must be <= end date {end_dt}")

        dates = []
        current = start_dt
        while current <= end_dt:
            # Skip weekends (5=Saturday, 6=Sunday)
            if current.weekday() < 5:
                dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return dates

    def _normalize_trade_record(self, symbol: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Convert raw Massive REST trade tick into schema dictionary."""
        sip_ts = raw.get("sip_timestamp") or raw.get("participant_timestamp") or 0
        timestamp = pd.to_datetime(sip_ts, unit="ns", errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.Timestamp.utcnow()

        seq = raw.get("sequence_number", 0)
        raw_id = raw.get("id")
        trade_id = str(raw_id) if raw_id is not None else f"{symbol}_{sip_ts}_{seq}"

        conditions = raw.get("conditions")
        cond_str = ",".join(str(c) for c in conditions) if isinstance(conditions, list) else (str(conditions) if conditions else "")

        return {
            "symbol": symbol.upper(),
            "timestamp": timestamp,
            "price": float(raw.get("price", 0.0)),
            "size": int(raw.get("size", 0)),
            "exchange": str(raw.get("exchange", "")),
            "conditions": cond_str,
            "trade_id": trade_id,
        }

    def _normalize_quote_record(self, symbol: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Convert raw Massive REST quote tick into schema dictionary."""
        sip_ts = raw.get("sip_timestamp") or raw.get("participant_timestamp") or 0
        timestamp = pd.to_datetime(sip_ts, unit="ns", errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.Timestamp.utcnow()

        seq = raw.get("sequence_number", 0)
        quote_id = f"{symbol}_{sip_ts}_{seq}"

        bx = raw.get("bid_exchange")
        ax = raw.get("ask_exchange")
        exchange_str = f"{bx}/{ax}" if bx is not None and ax is not None else (str(bx or ax or ""))

        return {
            "symbol": symbol.upper(),
            "timestamp": timestamp,
            "bid_price": float(raw.get("bid_price", 0.0)),
            "bid_size": int(raw.get("bid_size", 0)),
            "ask_price": float(raw.get("ask_price", 0.0)),
            "ask_size": int(raw.get("ask_size", 0)),
            "exchange": exchange_str,
            "quote_id": quote_id,
        }

    def ingest_day_trades(self, symbol: str, date_str: str) -> int:
        """Ingest all historical trades for a specific symbol on a given date."""
        symbol = symbol.upper()
        logger.info(f"Ingesting trades for {symbol} on {date_str}...")
        trades_batch: List[Dict[str, Any]] = []
        total_inserted = 0

        cursor: Optional[str] = None
        while True:
            data = self.client.fetch_trades_page(
                ticker=symbol,
                timestamp=date_str,
                cursor_url=cursor,
                limit=self.batch_size,
            )
            raw_results = data.get("results", [])
            for raw in raw_results:
                trades_batch.append(self._normalize_trade_record(symbol, raw))
                if len(trades_batch) >= self.batch_size:
                    total_inserted += self.storage.insert_trades_batch(trades_batch)
                    trades_batch.clear()

            cursor = data.get("next_url")
            if not cursor or not raw_results:
                break

        if trades_batch:
            total_inserted += self.storage.insert_trades_batch(trades_batch)
            trades_batch.clear()

        logger.info(f"Completed trades ingestion for {symbol} on {date_str}: {total_inserted} rows inserted")
        return total_inserted

    def ingest_day_quotes(self, symbol: str, date_str: str) -> int:
        """Ingest all historical NBBO quotes for a specific symbol on a given date."""
        symbol = symbol.upper()
        logger.info(f"Ingesting quotes for {symbol} on {date_str}...")
        quotes_batch: List[Dict[str, Any]] = []
        total_inserted = 0

        cursor: Optional[str] = None
        while True:
            data = self.client.fetch_quotes_page(
                ticker=symbol,
                timestamp=date_str,
                cursor_url=cursor,
                limit=self.batch_size,
            )
            raw_results = data.get("results", [])
            for raw in raw_results:
                quotes_batch.append(self._normalize_quote_record(symbol, raw))
                if len(quotes_batch) >= self.batch_size:
                    total_inserted += self.storage.insert_quotes_batch(quotes_batch)
                    quotes_batch.clear()

            cursor = data.get("next_url")
            if not cursor or not raw_results:
                break

        if quotes_batch:
            total_inserted += self.storage.insert_quotes_batch(quotes_batch)
            quotes_batch.clear()

        logger.info(f"Completed quotes ingestion for {symbol} on {date_str}: {total_inserted} rows inserted")
        return total_inserted

    def backfill(
        self,
        symbol: str,
        start_date: Union[str, date],
        end_date: Union[str, date],
        force: bool = False,
    ) -> Dict[str, Any]:
        """Backfill both trades and quotes across a date range with resume capability.

        Args:
            symbol: Stock ticker (e.g., 'AAPL')
            start_date: Start date string ('YYYY-MM-DD') or date object
            end_date: End date string ('YYYY-MM-DD') or date object
            force: If True, re-fetches data even if recorded in ingestion_log

        Returns:
            Execution summary dictionary.
        """
        symbol = symbol.upper()
        dates = self._generate_date_range(start_date, end_date)
        logger.info(f"Starting backfill for {symbol} across {len(dates)} trading days ({start_date} to {end_date})")

        days_processed = 0
        days_skipped = 0
        total_trades = 0
        total_quotes = 0
        errors: List[Dict[str, str]] = []

        for d in dates:
            if not force and self.storage.is_date_ingested(symbol, d):
                logger.info(f"Skipping {symbol} {d}: already logged in ingestion_log")
                days_skipped += 1
                continue

            try:
                t_count = self.ingest_day_trades(symbol, d)
                q_count = self.ingest_day_quotes(symbol, d)
                self.storage.record_ingestion_log(symbol, d, t_count, q_count)
                days_processed += 1
                total_trades += t_count
                total_quotes += q_count
            except Exception as e:
                err_msg = f"Failed to ingest {symbol} on {d}: {e}"
                logger.error(err_msg, exc_info=True)
                errors.append({"date": d, "error": str(e)})
                # Stop on error to allow clean resumption later
                break

        status = "completed" if not errors else "partial_error"
        summary = {
            "symbol": symbol,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "days_processed": days_processed,
            "days_skipped": days_skipped,
            "total_trades": total_trades,
            "total_quotes": total_quotes,
            "status": status,
            "errors": errors,
        }
        logger.info(f"Backfill finished: {summary}")
        return summary
