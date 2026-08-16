"""DuckDB storage and analytical persistence layer."""

from synthetic_depth.storage.duckdb_layer import DuckDBStorage
from synthetic_depth.storage.schema import (
    CREATE_TRADES_TABLE,
    CREATE_QUOTES_TABLE,
    CREATE_CLASSIFIED_TRADES_TABLE,
    CREATE_LIQUIDITY_METRICS_TABLE,
    CREATE_INGESTION_LOG_TABLE,
)

__all__ = [
    "DuckDBStorage",
    "CREATE_TRADES_TABLE",
    "CREATE_QUOTES_TABLE",
    "CREATE_CLASSIFIED_TRADES_TABLE",
    "CREATE_LIQUIDITY_METRICS_TABLE",
    "CREATE_INGESTION_LOG_TABLE",
]
