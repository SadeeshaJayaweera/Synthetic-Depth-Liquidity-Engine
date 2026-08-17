"""Dash Application Runner for Synthetic Depth Engine."""

import logging
from pathlib import Path
from dash import Dash

from synthetic_depth.dashboard.callbacks import register_callbacks
from synthetic_depth.dashboard.layout import build_dashboard_layout

logger = logging.getLogger(__name__)


def create_dashboard_app() -> Dash:
    """Instantiate and configure the Plotly Dash application."""
    assets_path = str(Path(__file__).parent / "assets")
    app = Dash(
        __name__,
        title="Synthetic Depth Engine - Institutional Terminal",
        assets_folder=assets_path,
        suppress_callback_exceptions=True,
    )

    app.layout = build_dashboard_layout()
    register_callbacks(app)

    return app


def run_dashboard(host: str = "0.0.0.0", port: int = 8050, debug: bool = False) -> None:
    """Launch the Dash dashboard server."""
    dash_app = create_dashboard_app()
    logger.info(f"Starting Synthetic Depth Engine Dashboard on http://{host}:{port}")
    dash_app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard(debug=True)
