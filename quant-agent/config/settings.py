"""
Central configuration for the Quant Agent system.

Reads from environment variables / `.env` via pydantic-settings. Nothing in
this module contains a hard-coded secret; see `.env.example` for the full
list of variables a deployment should set.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OperatingMode(str, Enum):
    """Human-approval ladder. See spec section 28.

    The system NEVER silently escalates itself up this ladder — a mode
    change is a deliberate config change made by a human operator.
    """

    FULL_AUTO_RESEARCH = "FULL_AUTO_RESEARCH"  # research + backtest only
    AUTO_PAPER = "AUTO_PAPER"                  # + auto-deploy to paper trading
    HUMAN_APPROVAL = "HUMAN_APPROVAL"          # + live requires human sign-off (default)
    FULL_AUTO_LIVE = "FULL_AUTO_LIVE"          # + live deploys without approval


class EmbeddingBackend(str, Enum):
    NONE = "none"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    database_url: str = Field(
        default="postgresql+psycopg://quant:quant@localhost:5432/quant_agent",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # --- Mode ---
    mode: OperatingMode = Field(default=OperatingMode.HUMAN_APPROVAL, alias="QUANT_AGENT_MODE")
    allow_full_auto_live: bool = Field(default=False, alias="ALLOW_FULL_AUTO_LIVE")

    # --- LLM ---
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="claude-sonnet-5", alias="LLM_MODEL")

    # --- Research sources ---
    semantic_scholar_api_key: str | None = Field(default=None, alias="SEMANTIC_SCHOLAR_API_KEY")
    crossref_mailto: str | None = Field(default=None, alias="CROSSREF_MAILTO")
    ssrn_api_key: str | None = Field(default=None, alias="SSRN_API_KEY")

    # --- Embeddings ---
    embedding_backend: EmbeddingBackend = Field(default=EmbeddingBackend.NONE, alias="EMBEDDING_BACKEND")
    qdrant_url: str | None = Field(default=None, alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")

    # --- Experiment tracking ---
    mlflow_tracking_uri: str | None = Field(default=None, alias="MLFLOW_TRACKING_URI")

    # --- Broker credentials (live execution not implemented; see execution/brokers) ---
    ibkr_host: str | None = Field(default=None, alias="IBKR_HOST")
    ibkr_port: int | None = Field(default=None, alias="IBKR_PORT")
    ibkr_client_id: int | None = Field(default=None, alias="IBKR_CLIENT_ID")
    tradovate_api_key: str | None = Field(default=None, alias="TRADOVATE_API_KEY")
    tradovate_api_secret: str | None = Field(default=None, alias="TRADOVATE_API_SECRET")
    ninjatrader_host: str | None = Field(default=None, alias="NINJATRADER_HOST")

    # --- Experiment budget (spec section 40) ---
    max_daily_experiments: int = Field(default=50, alias="MAX_DAILY_EXPERIMENTS")
    max_concurrent_backtests: int = Field(default=4, alias="MAX_CONCURRENT_BACKTESTS")
    max_strategy_variants_per_hypothesis: int = Field(default=8, alias="MAX_STRATEGY_VARIANTS_PER_HYPOTHESIS")
    max_parameter_search_size: int = Field(default=200, alias="MAX_PARAMETER_SEARCH_SIZE")
    max_llm_calls_per_day: int = Field(default=500, alias="MAX_LLM_CALLS_PER_DAY")
    max_research_papers_per_day: int = Field(default=100, alias="MAX_RESEARCH_PAPERS_PER_DAY")

    # --- Alerts ---
    alert_webhook_url: str | None = Field(default=None, alias="ALERT_WEBHOOK_URL")

    # --- Paper trading gate (spec section 17) ---
    paper_trading_min_days: int = Field(default=30, alias="PAPER_TRADING_MIN_DAYS")
    paper_trading_max_days: int = Field(default=90, alias="PAPER_TRADING_MAX_DAYS")
    paper_vs_expected_tolerance_pct: float = Field(
        default=40.0,
        alias="PAPER_VS_EXPECTED_TOLERANCE_PCT",
        description="Max allowed % deviation of paper Sharpe/PnL from backtest-expected before auto-halt.",
    )

    @property
    def has_llm(self) -> bool:
        return bool(self.anthropic_api_key)

    @model_validator(mode="after")
    def _validate_live_mode(self) -> "Settings":
        if self.mode == OperatingMode.FULL_AUTO_LIVE and not self.allow_full_auto_live:
            raise ValueError(
                "QUANT_AGENT_MODE=FULL_AUTO_LIVE requires ALLOW_FULL_AUTO_LIVE=true. "
                "This is a deliberate safety gate — see spec section 28/29."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
