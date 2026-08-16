"""DuckDB read/write storage layer for tick data, classified trades, and analytical aggregations.

Includes exponential jitter backoff and retry handling for multi-process lock contention.
"""

from datetime import datetime
from functools import wraps
from pathlib import Path
import random
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union
import logging
import duckdb
import pandas as pd

from synthetic_depth.config import get_settings
from synthetic_depth.storage.schema import (
    CREATE_TRADES_TABLE,
    CREATE_QUOTES_TABLE,
    CREATE_CLASSIFIED_TRADES_TABLE,
    CREATE_LIQUIDITY_METRICS_TABLE,
    CREATE_INGESTION_LOG_TABLE,
    CREATE_INDEXES,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def with_duckdb_retry(max_retries: int = 5, base_delay: float = 0.05, max_delay: float = 2.0) -> Callable[[F], F]:
    """Decorator to retry DuckDB operations on file lock contention with exponential jitter backoff."""

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (duckdb.IOException, duckdb.TransactionException, duckdb.ConnectionException) as exc:
                    last_exc = exc
                    delay = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0, 0.05)
                    logger.warning(
                        f"DuckDB lock contention during {func.__name__} (attempt {attempt + 1}/{max_retries}). "
                        f"Retrying in {delay:.3f}s: {exc}"
                    )
                    time.sleep(delay)
                except Exception:
                    raise

            logger.error(f"DuckDB operation {func.__name__} failed after {max_retries} attempts: {last_exc}")
            raise last_exc  # type: ignore

        return wrapper  # type: ignore

    return decorator


class DuckDBStorage:
    """Analytical storage manager backed by DuckDB with lock contention resilience."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None, read_only: bool = False):
        """Initialize DuckDB storage.

        Args:
            db_path: Path to database file or ':memory:'. If None, taken from settings.
            read_only: Whether to open in read-only mode.
        """
        if db_path is None:
            settings = get_settings()
            self.db_path = str(settings.duckdb_path)
        else:
            self.db_path = str(db_path)

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self.read_only = read_only
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._init_db()

    @with_duckdb_retry(max_retries=5)
    def _init_db(self) -> None:
        """Connect and initialize base tables and indexes if writable."""
        self._conn = duckdb.connect(database=self.db_path, read_only=self.read_only)
        if not self.read_only:
            self._conn.execute(CREATE_TRADES_TABLE)
            self._conn.execute(CREATE_QUOTES_TABLE)
            self._conn.execute(CREATE_CLASSIFIED_TRADES_TABLE)
            self._conn.execute(CREATE_LIQUIDITY_METRICS_TABLE)
            self._conn.execute(CREATE_INGESTION_LOG_TABLE)
            try:
                for statement in CREATE_INDEXES.strip().split(";"):
                    if statement.strip():
                        self._conn.execute(statement)
            except Exception as e:
                logger.debug(f"Index notice: {e}")
            logger.info(f"Initialized DuckDB schema at {self.db_path}")

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Retrieve active DuckDB connection."""
        if self._conn is None:
            self._init_db()
        return self._conn

    @with_duckdb_retry(max_retries=5)
    def insert_trades_dataframe(self, df: pd.DataFrame) -> int:
        """Bulk insert trade ticks from a Pandas DataFrame into DuckDB with idempotency."""
        if df is None or df.empty:
            return 0
        
        expected_cols = ["symbol", "timestamp", "price", "size", "exchange", "conditions", "trade_id"]
        df_to_insert = df[expected_cols].copy()
        
        conn = self.connection
        conn.register("df_trades_batch", df_to_insert)
        query = """
        INSERT OR IGNORE INTO trades (symbol, timestamp, price, size, exchange, conditions, trade_id)
        SELECT symbol, timestamp, price, size, exchange, conditions, trade_id 
        FROM df_trades_batch
        """
        conn.execute(query)
        conn.unregister("df_trades_batch")
        count = len(df_to_insert)
        logger.debug(f"Bulk inserted {count} trade ticks into DuckDB")
        return count

    def insert_trades_batch(self, records: List[Dict[str, Any]]) -> int:
        """Bulk insert a list of trade dictionaries into DuckDB."""
        if not records:
            return 0
        df = pd.DataFrame(records)
        return self.insert_trades_dataframe(df)

    @with_duckdb_retry(max_retries=5)
    def insert_quotes_dataframe(self, df: pd.DataFrame) -> int:
        """Bulk insert quote ticks from a Pandas DataFrame into DuckDB with idempotency."""
        if df is None or df.empty:
            return 0
        
        expected_cols = ["symbol", "timestamp", "bid_price", "bid_size", "ask_price", "ask_size", "exchange", "quote_id"]
        df_to_insert = df[expected_cols].copy()

        conn = self.connection
        conn.register("df_quotes_batch", df_to_insert)
        query = """
        INSERT OR IGNORE INTO quotes (symbol, timestamp, bid_price, bid_size, ask_price, ask_size, exchange, quote_id)
        SELECT symbol, timestamp, bid_price, bid_size, ask_price, ask_size, exchange, quote_id 
        FROM df_quotes_batch
        """
        conn.execute(query)
        conn.unregister("df_quotes_batch")
        count = len(df_to_insert)
        logger.debug(f"Bulk inserted {count} quote ticks into DuckDB")
        return count

    def insert_quotes_batch(self, records: List[Dict[str, Any]]) -> int:
        """Bulk insert a list of quote dictionaries into DuckDB."""
        if not records:
            return 0
        df = pd.DataFrame(records)
        return self.insert_quotes_dataframe(df)

    @with_duckdb_retry(max_retries=5)
    def insert_classified_trades_dataframe(self, df: pd.DataFrame) -> int:
        """Bulk insert classified trades from a Pandas DataFrame into DuckDB."""
        if df is None or df.empty:
            return 0

        expected_cols = [
            "symbol", "timestamp", "price", "size", "exchange", "conditions",
            "trade_id", "bid_price", "ask_price", "mid_price", "direction",
            "classification_method",
        ]
        df_to_insert = df[expected_cols].copy()

        conn = self.connection
        conn.register("df_classified_batch", df_to_insert)
        query = """
        INSERT OR REPLACE INTO classified_trades (
            symbol, timestamp, price, size, exchange, conditions,
            trade_id, bid_price, ask_price, mid_price, direction,
            classification_method
        )
        SELECT 
            symbol, timestamp, price, size, exchange, conditions,
            trade_id, bid_price, ask_price, mid_price, direction,
            classification_method
        FROM df_classified_batch
        """
        conn.execute(query)
        conn.unregister("df_classified_batch")
        count = len(df_to_insert)
        logger.debug(f"Inserted {count} classified trades into DuckDB")
        return count

    @with_duckdb_retry(max_retries=5)
    def insert_liquidity_metrics_dataframe(self, df: pd.DataFrame) -> int:
        """Bulk insert computed liquidity metrics into DuckDB."""
        if df is None or df.empty:
            return 0

        expected_cols = [
            "symbol", "timestamp", "kyle_lambda", "amihud_ratio",
            "replenishment_speed_ms", "effective_spread", "realized_spread",
            "vpin", "liquidity_score",
        ]
        df_to_insert = df[expected_cols].copy()

        conn = self.connection
        conn.register("df_metrics_batch", df_to_insert)
        query = """
        INSERT OR REPLACE INTO liquidity_metrics (
            symbol, timestamp, kyle_lambda, amihud_ratio,
            replenishment_speed_ms, effective_spread, realized_spread,
            vpin, liquidity_score
        )
        SELECT 
            symbol, timestamp, kyle_lambda, amihud_ratio,
            replenishment_speed_ms, effective_spread, realized_spread,
            vpin, liquidity_score
        FROM df_metrics_batch
        """
        conn.execute(query)
        conn.unregister("df_metrics_batch")
        count = len(df_to_insert)
        logger.debug(f"Inserted {count} liquidity metrics rows into DuckDB")
        return count

    @with_duckdb_retry(max_retries=5)
    def record_ingestion_log(
        self,
        symbol: str,
        date_str: str,
        trades_fetched: int,
        quotes_fetched: int,
        fetched_at: Optional[datetime] = None,
    ) -> None:
        """Record or update ingestion completion for a given symbol and date."""
        conn = self.connection
        fetch_time = fetched_at or datetime.utcnow()
        query = """
        INSERT OR REPLACE INTO ingestion_log (symbol, date, trades_fetched, quotes_fetched, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        """
        conn.execute(query, [symbol.upper(), date_str, trades_fetched, quotes_fetched, fetch_time])
        logger.debug(f"Recorded ingestion log: {symbol} on {date_str} (trades={trades_fetched}, quotes={quotes_fetched})")

    @with_duckdb_retry(max_retries=5)
    def is_date_ingested(self, symbol: str, date_str: str) -> bool:
        """Check if a date for a given symbol has already been successfully ingested."""
        conn = self.connection
        query = "SELECT COUNT(*) FROM ingestion_log WHERE symbol = ? AND date = ?"
        res = conn.execute(query, [symbol.upper(), date_str]).fetchone()
        return res is not None and res[0] > 0

    @with_duckdb_retry(max_retries=5)
    def get_ingested_dates(self, symbol: str) -> List[str]:
        """Get list of all dates ingested for a symbol."""
        conn = self.connection
        query = "SELECT date FROM ingestion_log WHERE symbol = ? ORDER BY date"
        rows = conn.execute(query, [symbol.upper()]).fetchall()
        return [r[0] for r in rows]

    @with_duckdb_retry(max_retries=5)
    def get_trades(
        self,
        symbol: str,
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Retrieve trades for a symbol within an optional time window."""
        conn = self.connection
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol.upper()]

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions)
        limit_clause = f" LIMIT {limit}" if limit else ""
        query = f"SELECT * FROM trades WHERE {where_clause} ORDER BY timestamp ASC{limit_clause}"
        return conn.execute(query, params).df()

    @with_duckdb_retry(max_retries=5)
    def get_quotes(
        self,
        symbol: str,
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Retrieve quotes for a symbol within an optional time window."""
        conn = self.connection
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol.upper()]

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions)
        limit_clause = f" LIMIT {limit}" if limit else ""
        query = f"SELECT * FROM quotes WHERE {where_clause} ORDER BY timestamp ASC{limit_clause}"
        return conn.execute(query, params).df()

    @with_duckdb_retry(max_retries=5)
    def get_classified_trades(
        self,
        symbol: str,
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Retrieve classified trades for a symbol within an optional time window."""
        conn = self.connection
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol.upper()]

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions)
        limit_clause = f" LIMIT {limit}" if limit else ""
        query = f"SELECT * FROM classified_trades WHERE {where_clause} ORDER BY timestamp ASC{limit_clause}"
        return conn.execute(query, params).df()

    @with_duckdb_retry(max_retries=5)
    def get_liquidity_metrics(
        self,
        symbol: str,
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Retrieve computed liquidity metrics for a symbol within an optional time window."""
        conn = self.connection
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol.upper()]

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions)
        limit_clause = f" LIMIT {limit}" if limit else ""
        query = f"SELECT * FROM liquidity_metrics WHERE {where_clause} ORDER BY timestamp ASC{limit_clause}"
        return conn.execute(query, params).df()

    @with_duckdb_retry(max_retries=5)
    def get_classification_stats(self, symbol: str, date_str: Optional[str] = None) -> Dict[str, Any]:
        """Compute summary statistics (buy/sell counts and volumes) for classified trades."""
        conn = self.connection
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol.upper()]

        if date_str:
            conditions.append("CAST(timestamp AS DATE) = CAST(? AS DATE)")
            params.append(date_str)

        where_clause = " AND ".join(conditions)
        query = f"""
        SELECT 
            COUNT(*) as total_trades,
            COALESCE(SUM(size), 0) as total_volume,
            COALESCE(SUM(CASE WHEN direction = 'BUY' THEN 1 ELSE 0 END), 0) as buy_trades,
            COALESCE(SUM(CASE WHEN direction = 'BUY' THEN size ELSE 0 END), 0) as buy_volume,
            COALESCE(SUM(CASE WHEN direction = 'SELL' THEN 1 ELSE 0 END), 0) as sell_trades,
            COALESCE(SUM(CASE WHEN direction = 'SELL' THEN size ELSE 0 END), 0) as sell_volume,
            COALESCE(SUM(CASE WHEN direction = 'UNKNOWN' THEN 1 ELSE 0 END), 0) as unknown_trades,
            COALESCE(SUM(CASE WHEN direction = 'UNKNOWN' THEN size ELSE 0 END), 0) as unknown_volume,
            COALESCE(SUM(CASE WHEN classification_method = 'QUOTE_RULE' THEN 1 ELSE 0 END), 0) as quote_rule_trades,
            COALESCE(SUM(CASE WHEN classification_method = 'TICK_RULE' THEN 1 ELSE 0 END), 0) as tick_rule_trades
        FROM classified_trades
        WHERE {where_clause}
        """
        row = conn.execute(query, params).fetchone()
        if not row or row[0] == 0:
            return {
                "symbol": symbol.upper(),
                "date": date_str,
                "total_trades": 0,
                "total_volume": 0,
                "buy_trades": 0,
                "sell_trades": 0,
                "unknown_trades": 0,
                "buy_pct": 0.0,
                "sell_pct": 0.0,
                "unknown_pct": 0.0,
                "buy_volume": 0,
                "sell_volume": 0,
                "unknown_volume": 0,
                "buy_volume_pct": 0.0,
                "sell_volume_pct": 0.0,
                "quote_rule_trades": 0,
                "tick_rule_trades": 0,
            }

        total_trades = row[0]
        total_vol = row[1]
        buy_trades = row[2]
        buy_vol = row[3]
        sell_trades = row[4]
        sell_vol = row[5]
        unknown_trades = row[6]
        unknown_vol = row[7]

        return {
            "symbol": symbol.upper(),
            "date": date_str,
            "total_trades": total_trades,
            "total_volume": total_vol,
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "unknown_trades": unknown_trades,
            "buy_pct": round((buy_trades / total_trades) * 100.0, 2),
            "sell_pct": round((sell_trades / total_trades) * 100.0, 2),
            "unknown_pct": round((unknown_trades / total_trades) * 100.0, 2),
            "buy_volume": buy_vol,
            "sell_volume": sell_vol,
            "unknown_volume": unknown_vol,
            "buy_volume_pct": round((buy_vol / total_vol) * 100.0, 2) if total_vol > 0 else 0.0,
            "sell_volume_pct": round((sell_vol / total_vol) * 100.0, 2) if total_vol > 0 else 0.0,
            "quote_rule_trades": row[8],
            "tick_rule_trades": row[9],
        }

    @with_duckdb_retry(max_retries=5)
    def query(self, sql: str, params: Optional[List[Any]] = None) -> pd.DataFrame:
        """Execute an arbitrary SQL query and return results as a Pandas DataFrame."""
        conn = self.connection
        if params:
            return conn.execute(sql, params).df()
        return conn.execute(sql).df()

    @with_duckdb_retry(max_retries=5)
    def get_table_counts(self) -> Dict[str, int]:
        """Get record counts for all core tables."""
        conn = self.connection
        trades_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        quotes_count = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        classified_count = conn.execute("SELECT COUNT(*) FROM classified_trades").fetchone()[0]
        metrics_count = conn.execute("SELECT COUNT(*) FROM liquidity_metrics").fetchone()[0]
        logs_count = conn.execute("SELECT COUNT(*) FROM ingestion_log").fetchone()[0]
        return {
            "trades": trades_count,
            "quotes": quotes_count,
            "classified_trades": classified_count,
            "liquidity_metrics": metrics_count,
            "ingestion_log": logs_count,
        }

    def close(self) -> None:
        """Close connection to database."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            logger.debug("Closed DuckDB connection")
