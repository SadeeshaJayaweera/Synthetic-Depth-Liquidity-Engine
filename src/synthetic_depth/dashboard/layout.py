"""Layout definitions for the Synthetic Depth Engine institutional terminal."""

from dash import dcc, html


def build_dashboard_layout() -> html.Div:
    """Construct the complete Dash dashboard layout."""
    return html.Div(
        className="terminal-container",
        children=[
            # 1. Header & Navigation Bar
            html.Div(
                className="terminal-header",
                children=[
                    html.Div(
                        className="brand-title",
                        children=[
                            html.Span("⚡ SYNTHETIC DEPTH ENGINE", style={"color": "#f8fafc"}),
                            html.Span("PRO TERMINAL", className="brand-badge"),
                        ],
                    ),
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "20px"},
                        children=[
                            html.Div(
                                style={"display": "flex", "alignItems": "center", "gap": "10px"},
                                children=[
                                    html.Span("Active Symbol:", style={"fontSize": "13px", "color": "#94a3b8", "fontWeight": "600"}),
                                    dcc.Dropdown(
                                        id="symbol-select",
                                        options=[
                                            {"label": "AAPL (Apple Inc. - Large Cap)", "value": "AAPL"},
                                            {"label": "XYZ_ILLIQ (Illiquid Small-Cap)", "value": "XYZ_ILLIQ"},
                                        ],
                                        value="AAPL",
                                        clearable=False,
                                        style={"width": "260px"},
                                        className="dash-dropdown",
                                    ),
                                ],
                            ),
                            html.Div(
                                style={"display": "flex", "alignItems": "center", "gap": "8px"},
                                children=[
                                    html.Span("Refresh:", style={"fontSize": "13px", "color": "#94a3b8"}),
                                    dcc.Dropdown(
                                        id="refresh-interval-select",
                                        options=[
                                            {"label": "1 sec (Live)", "value": 1000},
                                            {"label": "5 sec", "value": 5000},
                                            {"label": "15 sec", "value": 15000},
                                            {"label": "Paused", "value": 0},
                                        ],
                                        value=5000,
                                        clearable=False,
                                        style={"width": "140px"},
                                        className="dash-dropdown",
                                    ),
                                ],
                            ),
                            html.Div(
                                className="live-indicator",
                                children=[
                                    html.Div(className="live-pulse"),
                                    html.Span("FEED CONNECTED"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # 2. Multi-Symbol Watchlist Ribbon
            html.Div(
                className="watchlist-card",
                children=[
                    html.Div(className="watchlist-header", children="Market Liquidity Watchlist (Quick Compare)"),
                    html.Div(id="watchlist-container", className="watchlist-grid"),
                ],
            ),

            # 3. Main Dashboard 2-Column Grid
            html.Div(
                className="dashboard-grid",
                children=[
                    # LEFT COLUMN: Synthetic Depth Reconstructed Ladder
                    html.Div(
                        children=[
                            html.Div(
                                className="terminal-card",
                                children=[
                                    html.Div(
                                        className="card-header",
                                        children=[
                                            html.Div(
                                                className="card-title",
                                                children=[
                                                    html.Span("Reconstructed Synthetic Depth Ladder"),
                                                    html.Span("MODELED ESTIMATE", className="synthetic-pill"),
                                                ],
                                            ),
                                            html.Div(id="depth-levels-counter", style={"fontSize": "12px", "color": "#94a3b8"}),
                                        ],
                                    ),
                                    html.Div(
                                        className="disclaimer-banner",
                                        children=[
                                            html.Strong("⚠️ QUANTITATIVE DISCLAIMER: "),
                                            "This order book depth curve is mathematically modeled from NBBO top-of-book quotes, "
                                            "Kyle's lambda price impact, and quote replenishment dynamics. "
                                            "It is an analytical approximation, NOT real Level 2 or Level 3 exchange book data.",
                                        ],
                                    ),
                                    dcc.Graph(
                                        id="synthetic-depth-graph",
                                        config={"displayModeBar": False, "responsive": True},
                                    ),
                                ],
                            ),
                        ],
                    ),

                    # RIGHT COLUMN: Liquidity Time Series & Slippage Calculator Widget
                    html.Div(
                        children=[
                            # Liquidity Metrics Time Series
                            html.Div(
                                className="terminal-card",
                                children=[
                                    html.Div(
                                        className="card-header",
                                        children=[
                                            html.Div(className="card-title", children="LiquidityScore & Microstructure Dynamics"),
                                            html.Div(
                                                children=[
                                                    dcc.Checklist(
                                                        id="metric-overlays-select",
                                                        options=[
                                                            {"label": " Kyle's λ", "value": "kyle_lambda"},
                                                            {"label": " VPIN Toxicity", "value": "vpin"},
                                                            {"label": " Spread", "value": "effective_spread"},
                                                            {"label": " Amihud", "value": "amihud_ratio"},
                                                        ],
                                                        value=["kyle_lambda", "vpin"],
                                                        inline=True,
                                                        style={"color": "#94a3b8", "fontSize": "12px", "display": "flex", "gap": "12px"},
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    dcc.Graph(
                                        id="liquidity-time-series-graph",
                                        config={"displayModeBar": False, "responsive": True},
                                    ),
                                ],
                            ),

                            # Interactive Slippage Calculator Widget
                            html.Div(
                                className="terminal-card",
                                children=[
                                    html.Div(
                                        className="card-header",
                                        children=[
                                            html.Div(
                                                className="card-title",
                                                children=[
                                                    html.Span("Execution Slippage & TCA Calculator"),
                                                    html.Span("SIMULATED", className="synthetic-pill"),
                                                ],
                                            ),
                                            html.Div(id="calc-confidence-badge"),
                                        ],
                                    ),
                                    html.Div(
                                        className="slippage-controls",
                                        children=[
                                            html.Div(
                                                children=[
                                                    html.Div("Order Size (Shares)", className="input-label"),
                                                    dcc.Input(
                                                        id="slippage-shares-input",
                                                        type="number",
                                                        value=2500,
                                                        min=1,
                                                        step=100,
                                                        className="dash-input",
                                                        style={"width": "100%", "padding": "8px", "boxSizing": "border-box"},
                                                    ),
                                                    html.Div(
                                                        style={"display": "flex", "gap": "6px", "marginTop": "6px"},
                                                        children=[
                                                            html.Button("500", id="preset-500", className="preset-btn", n_clicks=0),
                                                            html.Button("1k", id="preset-1k", className="preset-btn", n_clicks=0),
                                                            html.Button("2.5k", id="preset-25k", className="preset-btn", n_clicks=0),
                                                            html.Button("5k", id="preset-5k", className="preset-btn", n_clicks=0),
                                                            html.Button("10k", id="preset-10k", className="preset-btn", n_clicks=0),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                children=[
                                                    html.Div("Execution Direction", className="input-label"),
                                                    dcc.RadioItems(
                                                        id="slippage-side-radio",
                                                        options=[
                                                            {"label": " BUY (Walk Asks)", "value": "BUY"},
                                                            {"label": " SELL (Walk Bids)", "value": "SELL"},
                                                        ],
                                                        value="BUY",
                                                        inline=True,
                                                        style={"color": "#f8fafc", "fontSize": "13px", "display": "flex", "gap": "16px", "paddingTop": "10px"},
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    # Calculation Results Output Grid
                                    html.Div(id="slippage-results-container", className="slippage-results-grid"),
                                    # Warnings container
                                    html.Div(id="slippage-warnings-container", style={"marginTop": "10px"}),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # Auto-Refresh Interval Component
            dcc.Interval(id="live-interval-timer", interval=5000, n_intervals=0),
        ],
    )
