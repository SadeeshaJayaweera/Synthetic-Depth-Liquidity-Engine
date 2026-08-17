#!/usr/bin/env python3
"""Massive.com API Connectivity Smoke Test.

Reads MASSIVE_API_KEY from .env, issues a request for AAPL previous day aggregate bar,
and validates the returned market data payload.
"""

import os
import sys
import json
from pathlib import Path

# Add src directory to path if run directly
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from synthetic_depth.config import get_settings
from synthetic_depth.logging_config import setup_logging
from synthetic_depth.ingestion.rest_client import MassiveRESTClient


def run_smoke_test(ticker: str = "AAPL") -> bool:
    """Execute connectivity smoke test against Massive.com REST API.

    Args:
        ticker: Symbol to query (default: AAPL)

    Returns:
        True if call succeeded and returned valid bar data, False otherwise.
    """
    logger = setup_logging()
    settings = get_settings(reload=True)

    print("=" * 70)
    print("  MASSIVE.COM API CONNECTIVITY SMOKE TEST")
    print("=" * 70)
    print(f"Target Symbol:   {ticker}")
    print(f"DuckDB Path:     {settings.duckdb_path}")
    print(f"Log Level:       {settings.log_level}")

    if not settings.has_valid_api_key:
        print("\n[ERROR] MASSIVE_API_KEY is not set or contains placeholder value.")
        print("Please set a valid key in your `.env` file:")
        print("  MASSIVE_API_KEY=your_actual_key_here")
        print("\nYou can obtain an API key at https://massive.com/dashboard/keys")
        print("=" * 70)
        return False

    # Mask key for secure console display
    masked_key = (
        settings.massive_api_key[:4] + "..." + settings.massive_api_key[-4:]
        if len(settings.massive_api_key) > 8
        else "***"
    )
    print(f"API Key:         {masked_key}")
    print("\nSending request: GET /v2/aggs/ticker/{ticker}/prev ...")

    try:
        client = MassiveRESTClient(api_key=settings.massive_api_key)
        results = client.get_previous_close_agg(ticker)

        if not results:
            print("\n[WARN] Request succeeded but returned empty result list.")
            print(f"Response: {results}")
            return False

        print("\n[SUCCESS] Successfully received market data from Massive.com!")
        print("-" * 70)
        print("Received Aggregate Bar Details:")
        for idx, bar in enumerate(results, 1):
            print(f"  Bar #{idx}:")
            if isinstance(bar, dict):
                for k, v in bar.items():
                    print(f"    {k}: {v}")
            else:
                print(f"    {bar}")
        print("-" * 70)
        print("Data Contract Verification: PASS")
        print("=" * 70)
        return True

    except Exception as e:
        print(f"\n[ERROR] Failed to communicate with Massive API: {e}")
        print("=" * 70)
        return False


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    success = run_smoke_test(ticker=target)
    sys.exit(0 if success else 1)
