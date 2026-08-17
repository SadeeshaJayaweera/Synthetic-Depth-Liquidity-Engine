"""Plotly chart generators and UI component factories for the synthetic depth dashboard."""

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from synthetic_depth.microstructure.synthetic_book import SyntheticOrderBook
from synthetic_depth.storage.duckdb_layer import DuckDBStorage


def create_synthetic_depth_chart(book: Optional[SyntheticOrderBook]) -> go.Figure:
    """Create a horizontal ladder depth curve chart with prominent synthetic distinction."""
    fig = go.Figure()

    if not book or (not book.bids and not book.asks):
        fig.add_annotation(
            text="No Synthetic Depth Data Available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color="#94a3b8"),
        )
        fig.update_layout(
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            margin=dict(l=20, r=20, t=30, b=20),
            height=420,
        )
        return fig

    # 1. Bids (left side, green)
    bid_prices = [f"${b.price:.2f}" for b in book.bids][::-1]
    bid_sizes = [-b.size for b in book.bids][::-1]
    bid_cum_sizes = [b.cumulative_size for b in book.bids][::-1]
    bid_colors = ["#10b981" if not b.is_synthetic else "rgba(16, 185, 129, 0.45)" for b in book.bids][::-1]
    bid_hover = [
        f"<b>Level {b.level} ({'Real NBBO Touch' if not b.is_synthetic else 'Modeled Synthetic Depth'})</b><br>"
        f"Price: ${b.price:.2f}<br>"
        f"Size: {b.size:,} shares<br>"
        f"Cumulative Depth: {b.cumulative_size:,} shares"
        for b in book.bids
    ][::-1]

    fig.add_trace(go.Bar(
        y=bid_prices,
        x=bid_sizes,
        orientation="h",
        name="Bids (Modeled Depth)",
        marker=dict(color=bid_colors, line=dict(color="#10b981", width=1)),
        hovertext=bid_hover,
        hoverinfo="text",
    ))

    # 2. Asks (right side, red)
    ask_prices = [f"${a.price:.2f}" for a in book.asks]
    ask_sizes = [a.size for a in book.asks]
    ask_cum_sizes = [a.cumulative_size for a in book.asks]
    ask_colors = ["#ef4444" if not a.is_synthetic else "rgba(239, 68, 68, 0.45)" for a in book.asks]
    ask_hover = [
        f"<b>Level {a.level} ({'Real NBBO Touch' if not a.is_synthetic else 'Modeled Synthetic Depth'})</b><br>"
        f"Price: ${a.price:.2f}<br>"
        f"Size: {a.size:,} shares<br>"
        f"Cumulative Depth: {a.cumulative_size:,} shares"
        for a in book.asks
    ]

    fig.add_trace(go.Bar(
        y=ask_prices,
        x=ask_sizes,
        orientation="h",
        name="Asks (Modeled Depth)",
        marker=dict(color=ask_colors, line=dict(color="#ef4444", width=1)),
        hovertext=ask_hover,
        hoverinfo="text",
    ))

    # Central watermark banner
    fig.add_annotation(
        text="MODELED SYNTHETIC DEPTH (NOT REAL L2)",
        xref="paper", yref="paper",
        x=0.5, y=0.92,
        showarrow=False,
        font=dict(size=12, color="rgba(245, 158, 11, 0.7)", family="monospace"),
        bgcolor="rgba(17, 24, 39, 0.85)",
        bordercolor="rgba(245, 158, 11, 0.4)",
        borderwidth=1,
        borderpad=4,
    )

    fig.update_layout(
        title=dict(
            text=f"<b>{book.symbol}</b> Synthetic Depth Ladder (Spread: ${book.spread:.4f} | Mid: ${book.mid_price:.4f})",
            font=dict(color="#f8fafc", size=13),
        ),
        paper_bgcolor="#111827",
        plot_bgcolor="#0d131f",
        margin=dict(l=50, r=40, t=50, b=30),
        height=420,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1.0,
            font=dict(color="#94a3b8", size=11),
        ),
        xaxis=dict(
            title="Estimated Size at Level (Shares)",
            color="#94a3b8",
            gridcolor="#1e293b",
            zerolinecolor="#334155",
            zerolinewidth=2,
        ),
        yaxis=dict(
            title="Price Level",
            color="#94a3b8",
            gridcolor="#1e293b",
        ),
        barmode="overlay",
    )

    return fig


def create_liquidity_time_series(
    df: pd.DataFrame,
    overlays: Optional[List[str]] = None,
) -> go.Figure:
    """Create interactive microstructure time-series chart with toggleable metric overlays."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    active = overlays or []

    if df is None or df.empty:
        fig.add_annotation(
            text="No Historical Liquidity Metrics Found",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color="#94a3b8"),
        )
        fig.update_layout(
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            margin=dict(l=20, r=20, t=30, b=20),
            height=380,
        )
        return fig

    timestamps = pd.to_datetime(df["timestamp"])

    # 1. Primary Trace: Composite LiquidityScore (0-100)
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=df["liquidity_score"],
            name="LiquidityScore (0-100)",
            line=dict(color="#06b6d4", width=2.5),
            fill="tozeroy",
            fillcolor="rgba(6, 182, 212, 0.08)",
        ),
        secondary_y=False,
    )

    # 2. Overlays
    if "kyle_lambda" in active and "kyle_lambda" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=df["kyle_lambda"],
                name="Kyle's Lambda ($/sh)",
                line=dict(color="#f59e0b", width=1.5, dash="dot"),
            ),
            secondary_y=True,
        )

    if "vpin" in active and "vpin" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=df["vpin"],
                name="VPIN Toxicity",
                line=dict(color="#ef4444", width=1.5, dash="dash"),
            ),
            secondary_y=True,
        )

    if "effective_spread" in active and "effective_spread" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=df["effective_spread"],
                name="Effective Spread ($)",
                line=dict(color="#8b5cf6", width=1.5),
            ),
            secondary_y=True,
        )

    if "amihud_ratio" in active and "amihud_ratio" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=df["amihud_ratio"],
                name="Amihud Ratio",
                line=dict(color="#10b981", width=1.5, dash="dot"),
            ),
            secondary_y=True,
        )

    fig.update_layout(
        paper_bgcolor="#111827",
        plot_bgcolor="#0d131f",
        margin=dict(l=40, r=40, t=30, b=30),
        height=380,
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1.0,
            font=dict(color="#94a3b8", size=11),
        ),
        xaxis=dict(
            color="#94a3b8",
            gridcolor="#1e293b",
            showgrid=True,
        ),
        yaxis=dict(
            title="LiquidityScore",
            range=[0, 100],
            color="#06b6d4",
            gridcolor="#1e293b",
        ),
        yaxis2=dict(
            title="Overlays",
            color="#94a3b8",
            showgrid=False,
            overlaying="y",
            side="right",
        ),
    )

    return fig


def get_watchlist_summary(storage: DuckDBStorage) -> List[Dict[str, Any]]:
    """Retrieve multi-symbol watchlist summary from DuckDB."""
    symbols = ["AAPL", "XYZ_ILLIQ"]
    watchlist = []

    for sym in symbols:
        m_df = storage.query(
            "SELECT * FROM liquidity_metrics WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            [sym],
        )
        q_df = storage.query(
            "SELECT * FROM quotes WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            [sym],
        )

        score = 50.0
        eff_spread = 0.01
        vpin = 0.05
        mid = 150.0

        if not m_df.empty:
            score = float(m_df.iloc[0]["liquidity_score"])
            eff_spread = float(m_df.iloc[0]["effective_spread"])
            vpin = float(m_df.iloc[0]["vpin"])

        if not q_df.empty:
            mid = round((float(q_df.iloc[0]["bid_price"]) + float(q_df.iloc[0]["ask_price"])) / 2.0, 2)

        watchlist.append({
            "symbol": sym,
            "mid_price": f"${mid:.2f}",
            "liquidity_score": round(score, 1),
            "effective_spread": f"${eff_spread:.4f}",
            "vpin": round(vpin, 4),
        })

    return watchlist
