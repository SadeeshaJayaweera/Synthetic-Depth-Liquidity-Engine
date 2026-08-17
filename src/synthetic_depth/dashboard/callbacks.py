"""Reactive callback handlers for the synthetic depth terminal."""

from typing import Any, Dict, List, Optional
from dash import Dash, Input, Output, State, ctx, html
import pandas as pd

from synthetic_depth.dashboard.components import (
    create_liquidity_time_series,
    create_synthetic_depth_chart,
    get_watchlist_summary,
)
from synthetic_depth.microstructure.synthetic_book import (
    ConfidenceLevel,
    SlippageEstimator,
    SyntheticDepthEstimator,
)
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


def register_callbacks(app: Dash) -> None:
    """Register all reactive Dash callbacks on the app instance."""

    # 1. Preset button handler for shares input
    @app.callback(
        Output("slippage-shares-input", "value"),
        [
            Input("preset-500", "n_clicks"),
            Input("preset-1k", "n_clicks"),
            Input("preset-25k", "n_clicks"),
            Input("preset-5k", "n_clicks"),
            Input("preset-10k", "n_clicks"),
        ],
        State("slippage-shares-input", "value"),
        prevent_initial_call=True,
    )
    def update_shares_preset(btn_500, btn_1k, btn_25k, btn_5k, btn_10k, current_val):
        triggered_id = ctx.triggered_id
        presets = {
            "preset-500": 500,
            "preset-1k": 1000,
            "preset-25k": 2500,
            "preset-5k": 5000,
            "preset-10k": 10000,
        }
        return presets.get(triggered_id, current_val or 2500)

    # 2. Timer interval update
    @app.callback(
        [Output("live-interval-timer", "interval"), Output("live-interval-timer", "disabled")],
        Input("refresh-interval-select", "value"),
    )
    def update_timer_interval(selected_ms):
        if selected_ms == 0:
            return 10000, True
        return selected_ms, False

    # 3. Main Data & Chart Updates (Synthetic Depth, Time Series, Watchlist)
    @app.callback(
        [
            Output("synthetic-depth-graph", "figure"),
            Output("liquidity-time-series-graph", "figure"),
            Output("watchlist-container", "children"),
            Output("depth-levels-counter", "children"),
        ],
        [
            Input("symbol-select", "value"),
            Input("metric-overlays-select", "value"),
            Input("live-interval-timer", "n_intervals"),
        ],
    )
    def update_terminal_charts(symbol, active_overlays, n_intervals):
        sym = (symbol or "AAPL").upper().strip()
        storage = DuckDBStorage()

        try:
            # 1. Synthetic Depth Book
            depth_estimator = SyntheticDepthEstimator(storage=storage)
            book = depth_estimator.get_latest_synthetic_book(symbol=sym, num_levels=10)
            depth_fig = create_synthetic_depth_chart(book)
            levels_text = f"Levels: 1 Real Touch + {len(book.bids)-1 if book else 0} Modeled Outer"

            # 2. Historical Liquidity Time Series
            metrics_df = storage.get_liquidity_metrics(symbol=sym, limit=500)
            ts_fig = create_liquidity_time_series(metrics_df, overlays=active_overlays)

            # 3. Watchlist Items
            watchlist_data = get_watchlist_summary(storage=storage)
            watchlist_items = []
            for item in watchlist_data:
                is_active = item["symbol"] == sym
                active_class = "watchlist-item active" if is_active else "watchlist-item"
                watchlist_items.append(
                    html.Div(
                        className=active_class,
                        children=[
                            html.Div(
                                style={"display": "flex", "justifyContent": "space-between", "marginBottom": "4px"},
                                children=[
                                    html.Span(item["symbol"], className="watchlist-sym"),
                                    html.Span(item["mid_price"], style={"color": "#94a3b8", "fontSize": "13px"}),
                                ],
                            ),
                            html.Div(
                                style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"},
                                children=[
                                    html.Span(f"Score: {item['liquidity_score']}/100", className="watchlist-score"),
                                    html.Span(f"Spread {item['effective_spread']}", style={"fontSize": "11px", "color": "#cbd5e1"}),
                                ],
                            ),
                        ],
                    )
                )

            return depth_fig, ts_fig, watchlist_items, levels_text

        finally:
            storage.close()

    # 4. Slippage Calculator Reactive Calculation
    @app.callback(
        [
            Output("slippage-results-container", "children"),
            Output("calc-confidence-badge", "children"),
            Output("slippage-warnings-container", "children"),
        ],
        [
            Input("symbol-select", "value"),
            Input("slippage-shares-input", "value"),
            Input("slippage-side-radio", "value"),
            Input("live-interval-timer", "n_intervals"),
        ],
    )
    def update_slippage_calc(symbol, shares, side, n_intervals):
        sym = (symbol or "AAPL").upper().strip()
        order_size = int(shares) if shares and shares > 0 else 1000
        order_side = side or "BUY"

        storage = DuckDBStorage()
        try:
            depth_estimator = SyntheticDepthEstimator(storage=storage)
            book = depth_estimator.get_latest_synthetic_book(symbol=sym, num_levels=25)

            if not book:
                empty_res = html.Div("No quote depth available for symbol", style={"color": "#ef4444"})
                return empty_res, html.Div(), html.Div()

            slippage_estimator = SlippageEstimator()
            estimate = slippage_estimator.estimate_slippage(
                synthetic_book=book,
                order_size=order_size,
                side=order_side,
            )

            # Confidence Badge
            badge_class = f"confidence-badge confidence-{estimate.confidence_level.value.lower().replace('_', '-')}"
            conf_badge = html.Span(
                f"Confidence: {estimate.confidence_level.value} ({estimate.confidence_score:.2f})",
                className=badge_class,
            )

            # Results Boxes
            boxes = [
                html.Div(
                    className="result-box",
                    children=[
                        html.Div("Avg Exec Price (VWAP)", className="result-title"),
                        html.Div(f"${estimate.average_execution_price:.4f}", className="result-val"),
                    ],
                ),
                html.Div(
                    className="result-box",
                    children=[
                        html.Div("Total Slippage (bps)", className="result-title"),
                        html.Div(
                            f"{estimate.slippage_bps:.2f} bps",
                            className="result-val",
                            style={"color": "#fbbf24" if estimate.slippage_bps > 5 else "#10b981"},
                        ),
                    ],
                ),
                html.Div(
                    className="result-box",
                    children=[
                        html.Div("Total Slippage ($)", className="result-title"),
                        html.Div(f"${estimate.slippage_dollars:.4f}", className="result-val"),
                    ],
                ),
                html.Div(
                    className="result-box",
                    children=[
                        html.Div("Half-Spread Cost", className="result-title"),
                        html.Div(f"{estimate.half_spread_cost_bps:.2f} bps", className="result-val"),
                    ],
                ),
                html.Div(
                    className="result-box",
                    children=[
                        html.Div("Depth Impact Cost", className="result-title"),
                        html.Div(f"{estimate.impact_cost_bps:.2f} bps", className="result-val"),
                    ],
                ),
                html.Div(
                    className="result-box",
                    children=[
                        html.Div("Levels Swept", className="result-title"),
                        html.Div(f"{estimate.levels_swept}", className="result-val"),
                    ],
                ),
            ]

            # Warnings
            warning_elements = []
            if estimate.warnings:
                for w in estimate.warnings:
                    warning_elements.append(
                        html.Div(
                            f"⚠️ {w}",
                            style={"color": "#fbbf24", "fontSize": "11px", "marginTop": "4px"},
                        )
                    )

            return boxes, conf_badge, warning_elements

        finally:
            storage.close()
