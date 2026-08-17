"""Background worker daemon for scheduled microstructure metric updates and nightly gap-fill backfills."""

from datetime import datetime, timedelta
import logging
import time
from typing import List, Optional
from apscheduler.schedulers.blocking import BlockingScheduler

from synthetic_depth.config import get_settings
from synthetic_depth.ingestion.historical import HistoricalIngestor
from synthetic_depth.ingestion.rest_client import MassiveRESTClient
from synthetic_depth.microstructure.classification import TradeClassifier
from synthetic_depth.microstructure.metrics import LiquidityMetricsCalculator
from synthetic_depth.storage.duckdb_layer import DuckDBStorage

logger = logging.getLogger(__name__)


class BackgroundMetricsWorker:
    """Computes and updates liquidity metrics periodically in the background."""

    def __init__(
        self,
        symbols: List[str],
        storage: Optional[DuckDBStorage] = None,
        bucket: str = "1min",
        lag_ms: float = 0.0,
    ):
        """Initialize BackgroundMetricsWorker.

        Args:
            symbols: List of ticker symbols to calculate metrics for.
            storage: DuckDBStorage instance.
            bucket: Aggregation time bucket ('1min', '5min').
            lag_ms: Quote reporting lag in ms for Lee-Ready classification.
        """
        self.symbols = [s.upper().strip() for s in symbols if s.strip()]
        self.storage = storage or DuckDBStorage()
        self.bucket = bucket
        self.lag_ms = lag_ms
        self.classifier = TradeClassifier(storage=self.storage)
        self.calculator = LiquidityMetricsCalculator(storage=self.storage)

    def run_metric_cycle(self, target_date: Optional[str] = None) -> None:
        """Execute one classification and metrics calculation cycle across all symbols."""
        today_str = target_date or datetime.utcnow().strftime("%Y-%m-%d")
        logger.info(f"Starting background metrics cycle for {len(self.symbols)} symbols on {today_str}")

        for symbol in self.symbols:
            try:
                # 1. Classify unclassified trades
                classified = self.classifier.classify_symbol_date(
                    symbol=symbol,
                    date_str=today_str,
                    lag_ms=self.lag_ms,
                    persist=True,
                )

                if classified.empty:
                    logger.debug(f"No trades to classify for {symbol} on {today_str}")
                    continue

                # 2. Compute liquidity metrics
                metrics_df = self.calculator.calculate_symbol_date(
                    symbol=symbol,
                    date_str=today_str,
                    time_bucket=self.bucket,
                    persist=True,
                )

                if not metrics_df.empty:
                    latest_score = float(metrics_df["liquidity_score"].iloc[-1])
                    logger.info(
                        f"Updated metrics for {symbol}: {len(metrics_df)} buckets | "
                        f"Latest LiquidityScore={latest_score:.2f}"
                    )

            except Exception as e:
                logger.error(f"Error computing background metrics for {symbol}: {e}", exc_info=True)


class NightlyGapFillWorker:
    """Backfills missing ticks from Massive REST API nightly to catch drops from live WebSocket streams."""

    def __init__(
        self,
        symbols: List[str],
        storage: Optional[DuckDBStorage] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize NightlyGapFillWorker."""
        settings = get_settings()
        self.symbols = [s.upper().strip() for s in symbols if s.strip()]
        self.api_key = api_key or settings.massive_api_key
        self.storage = storage or DuckDBStorage()
        self.rest_client = MassiveRESTClient(api_key=self.api_key) if self.api_key else None
        self.ingestor = HistoricalIngestor(rest_client=self.rest_client, storage=self.storage) if self.rest_client else None

    def run_gap_fill(self, date_str: Optional[str] = None) -> None:
        """Run gap fill backfill for yesterday's market data."""
        if not self.ingestor or not self.api_key:
            logger.warning("Nightly gap fill skipped: Massive API key not configured.")
            return

        target_date = date_str or (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        logger.info(f"Running nightly gap-fill backfill for symbols={self.symbols} on {target_date}")

        for symbol in self.symbols:
            try:
                summary = self.ingestor.backfill(
                    symbol=symbol,
                    start_date=target_date,
                    end_date=target_date,
                    force=False,
                )
                logger.info(f"Gap-fill complete for {symbol} on {target_date}: {summary}")
            except Exception as e:
                logger.error(f"Error during gap-fill for {symbol}: {e}", exc_info=True)


def start_worker_daemon(
    symbols: List[str],
    metrics_interval_seconds: int = 60,
    run_gap_fill_hour: int = 1,
) -> None:
    """Launch blocking APScheduler worker daemon."""
    logger.info(f"Initializing Synthetic Depth Engine Background Worker for symbols: {symbols}")
    storage = DuckDBStorage()
    metrics_worker = BackgroundMetricsWorker(symbols=symbols, storage=storage)
    gap_fill_worker = NightlyGapFillWorker(symbols=symbols, storage=storage)

    # Run one immediate metrics cycle on startup
    metrics_worker.run_metric_cycle()

    scheduler = BlockingScheduler()

    # Schedule periodic metrics calculation
    scheduler.add_job(
        metrics_worker.run_metric_cycle,
        "interval",
        seconds=metrics_interval_seconds,
        id="periodic_metrics_job",
        name="Compute Microstructure Metrics",
    )

    # Schedule nightly gap fill (01:00 UTC)
    scheduler.add_job(
        gap_fill_worker.run_gap_fill,
        "cron",
        hour=run_gap_fill_hour,
        minute=0,
        id="nightly_gap_fill_job",
        name="Nightly Massive Historical Gap Fill",
    )

    logger.info(f"Worker scheduler started. Metrics every {metrics_interval_seconds}s, Nightly gap fill at {run_gap_fill_hour:02d}:00 UTC.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker scheduler stopped.")
    finally:
        storage.close()
