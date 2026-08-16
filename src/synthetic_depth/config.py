"""Configuration loader using Pydantic Settings for Synthetic Depth Engine."""

from pathlib import Path
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Massive.com API Key
    massive_api_key: str = Field(
        default="",
        description="Massive.com (Polygon.io) API key for market data ingestion",
    )

    # DuckDB Database Location
    duckdb_path: Path = Field(
        default=Path("data/synthetic_depth.duckdb"),
        description="Path to local DuckDB database file",
    )

    # API Configuration
    api_keys: List[str] = Field(
        default=["dev-test-key-123", "prod-client-key-abc"],
        description="Allowed API keys for synthetic depth API service",
    )

    rate_limit_per_minute: int = Field(
        default=120,
        description="Maximum requests per minute per API key",
    )

    # Application Defaults
    log_level: str = Field(default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)")
    environment: str = Field(default="development", description="Execution environment (development, production)")

    @field_validator("massive_api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate API key format."""
        clean = v.strip()
        return clean

    @property
    def has_valid_api_key(self) -> bool:
        """Check if a real Massive API key has been configured (not default placeholder)."""
        return bool(self.massive_api_key and not self.massive_api_key.startswith("your_"))

    @property
    def is_api_key_configured(self) -> bool:
        """Alias for has_valid_api_key."""
        return self.has_valid_api_key


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Retrieve singleton Settings instance."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
