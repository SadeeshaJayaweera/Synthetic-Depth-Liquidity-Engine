"""Tests for configuration settings loading and validation."""

import os
from pathlib import Path
import pytest
from synthetic_depth.config import Settings, get_settings


def test_settings_default():
    """Test default settings without .env file."""
    settings = Settings(
        MASSIVE_API_KEY="test_key_12345",
        DUCKDB_PATH="data/test.duckdb",
        LOG_LEVEL="DEBUG",
    )
    assert settings.massive_api_key == "test_key_12345"
    assert str(settings.duckdb_path) == "data/test.duckdb"
    assert settings.log_level == "DEBUG"
    assert settings.has_valid_api_key is True


def test_settings_invalid_or_placeholder_key():
    """Test placeholder key detection."""
    settings = Settings(MASSIVE_API_KEY="your_massive_api_key_here")
    assert settings.has_valid_api_key is False

    empty_settings = Settings(MASSIVE_API_KEY="")
    assert empty_settings.has_valid_api_key is False
