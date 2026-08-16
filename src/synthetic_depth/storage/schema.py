"""DuckDB schema definitions for market trades, quotes, classified trades, liquidity metrics, and ingestion logs."""

CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    symbol TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    price DOUBLE NOT NULL,
    size INTEGER NOT NULL,
    exchange TEXT,
    conditions TEXT,
    trade_id TEXT PRIMARY KEY
);
"""

CREATE_QUOTES_TABLE = """
CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    bid_price DOUBLE NOT NULL,
    bid_size INTEGER NOT NULL,
    ask_price DOUBLE NOT NULL,
    ask_size INTEGER NOT NULL,
    exchange TEXT,
    quote_id TEXT PRIMARY KEY
);
"""

CREATE_CLASSIFIED_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS classified_trades (
    symbol TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    price DOUBLE NOT NULL,
    size INTEGER NOT NULL,
    exchange TEXT,
    conditions TEXT,
    trade_id TEXT PRIMARY KEY,
    bid_price DOUBLE,
    ask_price DOUBLE,
    mid_price DOUBLE,
    direction TEXT NOT NULL,
    classification_method TEXT NOT NULL,
    classified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_LIQUIDITY_METRICS_TABLE = """
CREATE TABLE IF NOT EXISTS liquidity_metrics (
    symbol TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    kyle_lambda DOUBLE,
    amihud_ratio DOUBLE,
    replenishment_speed_ms DOUBLE,
    effective_spread DOUBLE,
    realized_spread DOUBLE,
    vpin DOUBLE,
    liquidity_score DOUBLE,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, timestamp)
);
"""

CREATE_INGESTION_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS ingestion_log (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    trades_fetched INTEGER NOT NULL DEFAULT 0,
    quotes_fetched INTEGER NOT NULL DEFAULT 0,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, date)
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp ON trades (symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_quotes_symbol_timestamp ON quotes (symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_classified_trades_symbol_timestamp ON classified_trades (symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_liquidity_metrics_symbol_timestamp ON liquidity_metrics (symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_symbol_date ON ingestion_log (symbol, date);
"""
