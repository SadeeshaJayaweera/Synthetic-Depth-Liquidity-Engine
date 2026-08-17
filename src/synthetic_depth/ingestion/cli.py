"""CLI entrypoint for Synthetic Depth Engine data ingestion, classification, metrics, depth, and dashboard.

Supports historical backfill, live WebSocket streaming, trade classification, liquidity metrics,
synthetic depth reconstruction, execution slippage estimation, interactive terminal dashboard, and background worker daemon.
Usage:
    python -m synthetic_depth.ingestion.cli backfill --symbol AAPL --start 2026-07-01 --end 2026-08-01
    python -m synthetic_depth.ingestion.cli live --symbols AAPL,MSFT,TSLA
    python -m synthetic_depth.ingestion.cli classify --symbol AAPL --date 2026-07-06
    python -m synthetic_depth.ingestion.cli metrics --symbol AAPL --date 2026-07-06
    python -m synthetic_depth.ingestion.cli depth --symbol AAPL --levels 10
    python -m synthetic_depth.ingestion.cli slippage --symbol AAPL --shares 5000 --side BUY
    python -m synthetic_depth.ingestion.cli dashboard --port 8050
    python -m synthetic_depth.ingestion.cli worker --symbols AAPL,MSFT --interval 60
"""

import argparse
import sys
from datetime import datetime, timedelta
from typing import List, Optional

from synthetic_depth.config import get_settings
from synthetic_depth.logging_config import setup_logging
from synthetic_depth.storage.duckdb_layer import DuckDBStorage
from synthetic_depth.ingestion.rest_client import MassiveRESTClient
from synthetic_depth.ingestion.historical import HistoricalIngestor
from synthetic_depth.ingestion.live import LiveIngestor
from synthetic_depth.microstructure.classification import TradeClassifier
from synthetic_depth.microstructure.metrics import LiquidityMetricsCalculator
from synthetic_depth.microstructure.synthetic_book import (
    SyntheticDepthEstimator,
    SlippageEstimator,
)
from synthetic_depth.dashboard.app import run_dashboard
from synthetic_depth.worker.worker import start_worker_daemon


def handle_backfill(args: argparse.Namespace) -> int:
    """Execute historical backfill command."""
    logger = setup_logging(args.log_level)
    settings = get_settings()

    api_key = args.api_key or settings.massive_api_key
    if not api_key or not api_key.strip() or api_key.startswith("your_"):
        print("\n" + "=" * 70, file=sys.stderr)
        print("  [ERROR] MASSIVE_API_KEY is not configured.", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("Please set a valid key in your `.env` file:", file=sys.stderr)
        print("  MASSIVE_API_KEY=your_actual_key_here", file=sys.stderr)
        print("\nOr pass it via the CLI argument:", file=sys.stderr)
        print("  --api-key YOUR_KEY", file=sys.stderr)
        print("\nYou can obtain an API key at https://massive.com/dashboard/keys", file=sys.stderr)
        print("=" * 70 + "\n", file=sys.stderr)
        return 1

    db_path = args.db_path or str(settings.duckdb_path)
    storage = DuckDBStorage(db_path=db_path)

    rest_client = MassiveRESTClient(
        api_key=api_key,
        request_delay=args.rate_limit_delay,
    )

    ingestor = HistoricalIngestor(
        rest_client=rest_client,
        storage=storage,
        batch_size=args.batch_size,
    )

    symbol = args.symbol.upper()
    start_date = args.start
    end_date = args.end or start_date

    print("=" * 70)
    print("  SYNTHETIC DEPTH ENGINE - HISTORICAL BACKFILL")
    print("=" * 70)
    print(f"Symbol:        {symbol}")
    print(f"Date Range:    {start_date} -> {end_date}")
    print(f"DuckDB Path:   {db_path}")
    print(f"Force Re-fetch:{args.force}")
    print("=" * 70)

    try:
        summary = ingestor.backfill(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            force=args.force,
        )

        counts = storage.get_table_counts()
        print("\n" + "=" * 70)
        print("  BACKFILL SUMMARY")
        print("=" * 70)
        print(f"Status:          {summary['status'].upper()}")
        print(f"Days Processed:  {summary['days_processed']}")
        print(f"Days Skipped:    {summary['days_skipped']}")
        print(f"Trades Ingested: {summary['total_trades']:,}")
        print(f"Quotes Ingested: {summary['total_quotes']:,}")
        print("-" * 70)
        print("Current DuckDB Record Totals:")
        print(f"  Trades Table:     {counts['trades']:,}")
        print(f"  Quotes Table:     {counts['quotes']:,}")
        print(f"  Classified Table: {counts['classified_trades']:,}")
        print(f"  Liquidity Table:  {counts['liquidity_metrics']:,}")
        print(f"  Ingestion Log:    {counts['ingestion_log']:,}")
        print("=" * 70)

        return 0 if summary["status"] == "completed" else 1

    except Exception as e:
        logger.error(f"Backfill execution failed: {e}", exc_info=True)
        return 1
    finally:
        storage.close()


def handle_live(args: argparse.Namespace) -> int:
    """Execute live WebSocket ingestion command."""
    logger = setup_logging(args.log_level)
    settings = get_settings()

    api_key = args.api_key or settings.massive_api_key
    if not api_key or not api_key.strip() or api_key.startswith("your_"):
        print("\n" + "=" * 70, file=sys.stderr)
        print("  [ERROR] MASSIVE_API_KEY is not configured.", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("Please set a valid key in your `.env` file:", file=sys.stderr)
        print("  MASSIVE_API_KEY=your_actual_key_here", file=sys.stderr)
        print("\nOr pass it via the CLI argument:", file=sys.stderr)
        print("  --api-key YOUR_KEY", file=sys.stderr)
        print("\nYou can obtain an API key at https://massive.com/dashboard/keys", file=sys.stderr)
        print("=" * 70 + "\n", file=sys.stderr)
        return 1

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("[ERROR] No valid symbols provided. Example: --symbols AAPL,MSFT,TSLA", file=sys.stderr)
        return 1

    db_path = args.db_path or str(settings.duckdb_path)
    storage = DuckDBStorage(db_path=db_path)

    print("=" * 70)
    print("  SYNTHETIC DEPTH ENGINE - LIVE STREAMING INGESTION")
    print("=" * 70)
    print(f"Symbols:       {', '.join(symbols)}")
    print(f"Delayed Feed:  {args.delayed}")
    print(f"DuckDB Path:   {db_path}")
    print(f"Batch Size:    {args.batch_size}")
    print(f"Flush Interval:{args.flush_interval}s")
    print("=" * 70)
    print("Press Ctrl+C to gracefully stop streaming.\n")

    ingestor = LiveIngestor(
        symbols=symbols,
        api_key=api_key,
        storage=storage,
        delayed=args.delayed,
        batch_size=args.batch_size,
        flush_interval_seconds=args.flush_interval,
    )

    try:
        ingestor.run()
        return 0
    except KeyboardInterrupt:
        print("\nStopping live ingestor...")
        ingestor.stop()
        return 0
    finally:
        storage.close()


def handle_classify(args: argparse.Namespace) -> int:
    """Execute trade classification on stored DuckDB tick data."""
    logger = setup_logging(args.log_level)
    settings = get_settings()

    db_path = args.db_path or str(settings.duckdb_path)
    storage = DuckDBStorage(db_path=db_path)

    symbol = args.symbol.upper().strip()
    date_str = args.date.strip()

    print("=" * 70)
    print("  SYNTHETIC DEPTH ENGINE - TRADE CLASSIFICATION (LEE-READY)")
    print("=" * 70)
    print(f"Symbol:          {symbol}")
    print(f"Date:            {date_str}")
    print(f"Quote Lag:       {args.lag_ms} ms")
    print(f"DuckDB Path:     {db_path}")
    print("=" * 70)

    try:
        classifier = TradeClassifier(storage=storage)
        classified = classifier.classify_symbol_date(
            symbol=symbol,
            date_str=date_str,
            lag_ms=args.lag_ms,
            persist=True,
        )

        if classified.empty:
            print(f"\n[WARN] No raw trades found for {symbol} on {date_str}. Ingest data first via 'backfill'.")
            return 1

        stats = storage.get_classification_stats(symbol=symbol, date_str=date_str)

        print("\n" + "=" * 70)
        print("  CLASSIFICATION RESULTS")
        print("=" * 70)
        print(f"Total Trades:       {stats['total_trades']:,}")
        print(f"Total Volume:       {stats['total_volume']:,}")
        print("-" * 70)
        print(f"BUY Trades:         {stats['buy_trades']:,} ({stats['buy_pct']}%)")
        print(f"BUY Volume:         {stats['buy_volume']:,} ({stats['buy_volume_pct']}%)")
        print(f"SELL Trades:        {stats['sell_trades']:,} ({stats['sell_pct']}%)")
        print(f"SELL Volume:        {stats['sell_volume']:,} ({stats['sell_volume_pct']}%)")
        print(f"UNKNOWN Trades:     {stats['unknown_trades']:,} ({stats['unknown_pct']}%)")
        print("-" * 70)
        print(f"Quote Rule Matches: {stats['quote_rule_trades']:,}")
        print(f"Tick Rule Matches:  {stats['tick_rule_trades']:,}")
        print("=" * 70)

        if args.bvc:
            print("\n" + "=" * 70)
            print("  BULK VOLUME CLASSIFICATION (BVC) 1-MIN BARS (FIRST 5 BARS)")
            print("=" * 70)
            bvc_df = classifier.compute_bvc_bars(symbol=symbol, date_str=date_str, time_bucket="1min")
            if not bvc_df.empty:
                sample_cols = ["timestamp", "close", "volume", "buy_volume", "sell_volume", "buy_ratio"]
                print(bvc_df[sample_cols].head(5).to_string(index=False))
            print("=" * 70)

        return 0

    except Exception as e:
        logger.error(f"Classification failed: {e}", exc_info=True)
        return 1
    finally:
        storage.close()


def handle_metrics(args: argparse.Namespace) -> int:
    """Execute liquidity metrics calculation."""
    logger = setup_logging(args.log_level)
    settings = get_settings()

    db_path = args.db_path or str(settings.duckdb_path)
    storage = DuckDBStorage(db_path=db_path)

    symbol = args.symbol.upper().strip()
    date_str = args.date.strip()
    bucket = args.bucket

    print("=" * 70)
    print("  SYNTHETIC DEPTH ENGINE - LIQUIDITY METRICS EXTRACTION")
    print("=" * 70)
    print(f"Symbol:          {symbol}")
    print(f"Date:            {date_str}")
    print(f"Time Bucket:     {bucket}")
    print(f"DuckDB Path:     {db_path}")
    print("=" * 70)

    try:
        calc = LiquidityMetricsCalculator(storage=storage)
        metrics_df = calc.calculate_symbol_date(
            symbol=symbol,
            date_str=date_str,
            time_bucket=bucket,
            persist=True,
        )

        if metrics_df.empty:
            print(f"\n[WARN] No metrics computed for {symbol} on {date_str}. Make sure classified trades exist.")
            return 1

        print("\n" + "=" * 70)
        print(f"  SUMMARY OF LIQUIDITY METRICS ({symbol} - {date_str})")
        print("=" * 70)
        print(f"Time Buckets Computed:    {len(metrics_df)}")
        print(f"Mean Liquidity Score:     {metrics_df['liquidity_score'].mean():.2f} / 100")
        print(f"Mean Kyle's Lambda:       {metrics_df['kyle_lambda'].mean():.6f} $/share")
        print(f"Mean Amihud Illiquidity:  {metrics_df['amihud_ratio'].mean():.4f}")
        print(f"Replenishment Speed:      {metrics_df['replenishment_speed_ms'].iloc[0]:.1f} ms")
        print(f"Mean Effective Spread:    ${metrics_df['effective_spread'].mean():.4f}")
        print(f"Mean Realized Spread:     ${metrics_df['realized_spread'].mean():.4f}")
        print(f"Mean VPIN (Toxicity):     {metrics_df['vpin'].mean():.4f}")
        print("-" * 70)
        print("First 5 Time Buckets:")
        display_cols = [
            "timestamp", "kyle_lambda", "amihud_ratio",
            "effective_spread", "vpin", "liquidity_score",
        ]
        print(metrics_df[display_cols].head(5).to_string(index=False))
        print("=" * 70)

        return 0

    except Exception as e:
        logger.error(f"Metrics calculation failed: {e}", exc_info=True)
        return 1
    finally:
        storage.close()


def handle_depth(args: argparse.Namespace) -> int:
    """Execute synthetic depth reconstruction command."""
    logger = setup_logging(args.log_level)
    settings = get_settings()

    db_path = args.db_path or str(settings.duckdb_path)
    storage = DuckDBStorage(db_path=db_path)

    symbol = args.symbol.upper().strip()

    try:
        estimator = SyntheticDepthEstimator(storage=storage)
        book = estimator.get_latest_synthetic_book(
            symbol=symbol,
            num_levels=args.levels,
            tick_size=args.tick_size,
        )

        if not book:
            print(f"\n[WARN] No quotes available in DuckDB for symbol {symbol}. Run backfill first.")
            return 1

        print("=" * 70)
        print(f"  SYNTHETIC DEPTH RECONSTRUCTION: {symbol}")
        print("=" * 70)
        print(f"Timestamp:       {book.timestamp}")
        print(f"NBBO Touch:      Bid ${book.bid_price:.2f}  |  Ask ${book.ask_price:.2f}  (Spread: ${book.spread:.4f})")
        print(f"Midpoint:        ${book.mid_price:.4f}")
        print(f"Kyle's Lambda:   {book.kyle_lambda:.6f} $/share")
        print(f"Replenishment:   {book.replenishment_speed_ms:.1f} ms")
        print(f"Data Type:       {book.data_type.upper()} (is_synthetic={book.is_synthetic})")
        print("-" * 70)
        print(f"{'[BIDS - Modeled Depth]':<35} | {'[ASKS - Modeled Depth]':<35}")
        print(f"{'Lvl  Price    Size    CumSize':<35} | {'Lvl  Price    Size    CumSize':<35}")
        print("-" * 70)

        max_levels = max(len(book.bids), len(book.asks))
        for i in range(max_levels):
            bid_str = ""
            if i < len(book.bids):
                b = book.bids[i]
                bid_str = f"L{b.level:<2} ${b.price:<6.2f} {b.size:<6,d} {b.cumulative_size:<7,d}"

            ask_str = ""
            if i < len(book.asks):
                a = book.asks[i]
                ask_str = f"L{a.level:<2} ${a.price:<6.2f} {a.size:<6,d} {a.cumulative_size:<7,d}"

            print(f"{bid_str:<35} | {ask_str:<35}")

        print("=" * 70)
        print(f"NOTE: {book.disclaimer}\n")
        return 0

    except Exception as e:
        logger.error(f"Depth reconstruction failed: {e}", exc_info=True)
        return 1
    finally:
        storage.close()


def handle_slippage(args: argparse.Namespace) -> int:
    """Execute execution slippage estimation command."""
    logger = setup_logging(args.log_level)
    settings = get_settings()

    db_path = args.db_path or str(settings.duckdb_path)
    storage = DuckDBStorage(db_path=db_path)

    symbol = args.symbol.upper().strip()
    shares = args.shares
    side = args.side.upper().strip()

    try:
        depth_estimator = SyntheticDepthEstimator(storage=storage)
        book = depth_estimator.get_latest_synthetic_book(symbol=symbol, num_levels=25)

        if not book:
            print(f"\n[WARN] No quotes available in DuckDB for symbol {symbol}. Run backfill first.")
            return 1

        slippage_estimator = SlippageEstimator()
        estimate = slippage_estimator.estimate_slippage(
            synthetic_book=book,
            order_size=shares,
            side=side,
        )

        print("=" * 70)
        print(f"  SYNTHETIC SLIPPAGE ESTIMATION: {symbol} ({side} {shares:,} shares)")
        print("=" * 70)
        print(f"Midpoint Price:           ${estimate.midpoint_price:.4f}")
        print(f"Estimated Avg Exec Price: ${estimate.average_execution_price:.4f}")
        print(f"Total Slippage ($/sh):    ${estimate.slippage_dollars:.4f}")
        print(f"Total Slippage (bps):     {estimate.slippage_bps:.2f} bps")
        print("-" * 70)
        print(f"  - Half-Spread Cost:     {estimate.half_spread_cost_bps:.2f} bps")
        print(f"  - Impact Cost (Depth):  {estimate.impact_cost_bps:.2f} bps")
        print(f"Levels Swept:             {estimate.levels_swept}")
        print(f"Confidence Level:         {estimate.confidence_level.value} (Score: {estimate.confidence_score:.2f})")
        if estimate.warnings:
            print("-" * 70)
            print("Model Warnings & Flags:")
            for w in estimate.warnings:
                print(f"  [!] {w}")
        print("=" * 70)
        print(f"NOTE: {estimate.disclaimer}\n")
        return 0

    except Exception as e:
        logger.error(f"Slippage estimation failed: {e}", exc_info=True)
        return 1
    finally:
        storage.close()


def handle_dashboard(args: argparse.Namespace) -> int:
    """Launch the interactive Plotly Dash terminal dashboard."""
    setup_logging(args.log_level)
    print("=" * 70)
    print("  SYNTHETIC DEPTH ENGINE - LAUNCHING INSTITUTIONAL DASHBOARD")
    print("=" * 70)
    print(f"Dashboard URL:  http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop the dashboard server.\n")
    try:
        run_dashboard(host=args.host, port=args.port, debug=args.debug)
        return 0
    except KeyboardInterrupt:
        print("\nDashboard server stopped.")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Failed to start dashboard: {e}", file=sys.stderr)
        return 1


def handle_worker(args: argparse.Namespace) -> int:
    """Launch background worker daemon for periodic metrics and nightly gap fills."""
    setup_logging(args.log_level)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("[ERROR] No valid symbols provided for worker. Example: --symbols AAPL,MSFT", file=sys.stderr)
        return 1

    print("=" * 70)
    print("  SYNTHETIC DEPTH ENGINE - BACKGROUND WORKER DAEMON")
    print("=" * 70)
    print(f"Tracked Symbols:   {', '.join(symbols)}")
    print(f"Metrics Interval:  {args.interval}s")
    print(f"Nightly Gap Hour:  {args.gap_hour:02d}:00 UTC")
    print("=" * 70)
    print("Press Ctrl+C to stop the worker daemon.\n")
    try:
        start_worker_daemon(
            symbols=symbols,
            metrics_interval_seconds=args.interval,
            run_gap_fill_hour=args.gap_hour,
        )
        return 0
    except KeyboardInterrupt:
        print("\nWorker daemon stopped.")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Worker daemon crashed: {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m synthetic_depth.ingestion.cli",
        description="Synthetic Depth Engine - Market Data Ingestion & Analytics CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Command mode")

    # Backfill command
    backfill_parser = subparsers.add_parser("backfill", help="Backfill historical trades and quotes")
    backfill_parser.add_argument("--symbol", "-s", required=True, help="Stock ticker symbol (e.g. AAPL)")
    backfill_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    backfill_parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD, defaults to start date)")
    backfill_parser.add_argument("--force", action="store_true", help="Force re-ingest even if already logged")
    backfill_parser.add_argument("--rate-limit-delay", type=float, default=0.0, help="Delay between requests in seconds")
    backfill_parser.add_argument("--batch-size", type=int, default=25000, help="Batch insertion size")
    backfill_parser.add_argument("--db-path", default=None, help="DuckDB database path override")
    backfill_parser.add_argument("--api-key", default=None, help="Massive API key override")
    backfill_parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Live command
    live_parser = subparsers.add_parser("live", help="Stream real-time trades and quotes via WebSocket")
    live_parser.add_argument("--symbols", required=True, help="Comma-separated ticker symbols (e.g. AAPL,MSFT)")
    live_parser.add_argument("--delayed", action="store_true", help="Connect to 15-minute delayed stream")
    live_parser.add_argument("--batch-size", type=int, default=100, help="Batch size threshold for DB flush")
    live_parser.add_argument("--flush-interval", type=float, default=1.5, help="Flush interval in seconds")
    live_parser.add_argument("--db-path", default=None, help="DuckDB database path override")
    live_parser.add_argument("--api-key", default=None, help="Massive API key override")
    live_parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Classify command
    classify_parser = subparsers.add_parser("classify", help="Classify stored trades using Lee-Ready & BVC")
    classify_parser.add_argument("--symbol", "-s", required=True, help="Stock ticker symbol (e.g. AAPL)")
    classify_parser.add_argument("--date", "-d", required=True, help="Trading date (YYYY-MM-DD)")
    classify_parser.add_argument("--lag-ms", type=float, default=0.0, help="Quote reporting lag in milliseconds")
    classify_parser.add_argument("--bvc", action="store_true", help="Also compute Bulk Volume Classification (BVC)")
    classify_parser.add_argument("--db-path", default=None, help="DuckDB database path override")
    classify_parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Metrics command
    metrics_parser = subparsers.add_parser("metrics", help="Compute microstructure liquidity metrics")
    metrics_parser.add_argument("--symbol", "-s", required=True, help="Stock ticker symbol (e.g. AAPL)")
    metrics_parser.add_argument("--date", "-d", required=True, help="Trading date (YYYY-MM-DD)")
    metrics_parser.add_argument("--bucket", "-b", default="1min", help="Time bucket frequency (e.g. 1min, 5min)")
    metrics_parser.add_argument("--db-path", default=None, help="DuckDB database path override")
    metrics_parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Depth command
    depth_parser = subparsers.add_parser("depth", help="Display reconstructed synthetic order book depth")
    depth_parser.add_argument("--symbol", "-s", required=True, help="Stock ticker symbol (e.g. AAPL)")
    depth_parser.add_argument("--levels", "-l", type=int, default=10, help="Number of synthetic depth levels")
    depth_parser.add_argument("--tick-size", type=float, default=0.01, help="Price increment between levels")
    depth_parser.add_argument("--db-path", default=None, help="DuckDB database path override")
    depth_parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Slippage command
    slippage_parser = subparsers.add_parser("slippage", help="Estimate execution slippage and cost for an order size")
    slippage_parser.add_argument("--symbol", "-s", required=True, help="Stock ticker symbol (e.g. AAPL)")
    slippage_parser.add_argument("--shares", "-n", type=int, required=True, help="Order size in shares")
    slippage_parser.add_argument("--side", default="BUY", choices=["BUY", "SELL"], help="Order direction (BUY or SELL)")
    slippage_parser.add_argument("--db-path", default=None, help="DuckDB database path override")
    slippage_parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Dashboard command
    dash_parser = subparsers.add_parser("dashboard", help="Launch interactive Plotly Dash terminal")
    dash_parser.add_argument("--host", default="0.0.0.0", help="Host address to bind (default: 0.0.0.0)")
    dash_parser.add_argument("--port", type=int, default=8050, help="Port to bind (default: 8050)")
    dash_parser.add_argument("--debug", action="store_true", help="Enable Dash debug mode")
    dash_parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")

    # Worker command
    worker_parser = subparsers.add_parser("worker", help="Launch background metrics & gap-fill worker")
    worker_parser.add_argument("--symbols", required=True, help="Comma-separated symbols (e.g. AAPL,MSFT)")
    worker_parser.add_argument("--interval", type=int, default=60, help="Metrics calculation interval in seconds")
    worker_parser.add_argument("--gap-hour", type=int, default=1, help="UTC hour to trigger nightly gap fill (0-23)")
    worker_parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "backfill":
        return handle_backfill(args)
    elif args.command == "live":
        return handle_live(args)
    elif args.command == "classify":
        return handle_classify(args)
    elif args.command == "metrics":
        return handle_metrics(args)
    elif args.command == "depth":
        return handle_depth(args)
    elif args.command == "slippage":
        return handle_slippage(args)
    elif args.command == "dashboard":
        return handle_dashboard(args)
    elif args.command == "worker":
        return handle_worker(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
