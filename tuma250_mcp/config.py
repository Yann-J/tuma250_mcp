"""Configuration for the Tuma250 MCP server."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Tuma250Settings(BaseSettings):
    """
    Settings loaded from environment variables (or a .env file).

    All keys are prefixed with TUMA250_ in the environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TUMA250_",
        extra="ignore",
    )

    base_url: str = "https://tuma250.com"
    username: str
    password: str

    # Path to persist the Playwright browser storage state (cookies + localStorage).
    session_file: str = ".tuma250_session.json"

    # When True, Playwright runs in headed mode — useful for debugging selectors.
    debug: bool = False


def get_settings() -> Tuma250Settings:
    """
    Return a cached Tuma250Settings instance.

    Returns:
        Tuma250Settings: Validated settings from environment / .env file.
    """
    return Tuma250Settings()  # type: ignore[call-arg]
