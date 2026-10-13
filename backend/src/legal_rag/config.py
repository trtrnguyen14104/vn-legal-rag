from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    environment: str = "development"
    public_origin: str = "http://localhost:3000"
    database_url: SecretStr
    redis_url: SecretStr
    session_secret: SecretStr
    embedding_api_key: SecretStr
    embedding_url: str = "http://embedding:8001"
    embedding_document_timeout_seconds: float = Field(default=600, gt=0, le=1800)
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:3000/api/v1/auth/callback"
    admin_google_subjects: str = ""
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"
    jev_ai_api_key: SecretStr = SecretStr("")
    jev_ai_model: str = "jev-latest"
    jev_ai_base_url: str = "https://jev-ai.pro/api"
    jev_ai_inference_enabled: bool = False
    jev_ai_max_calls_per_query: int = Field(default=32, ge=0, le=100)
    jev_ai_max_calls_per_benchmark: int = Field(default=0, ge=0, le=100000)
    hf_token: SecretStr = SecretStr("")
    hf_buckets: str = "trtrnguyen14104/vietnamese-legal-documents-bucket,trtrnguyen14104/UTS_VLC-bucket"
    hf_document_limit: int = 100
    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 16
    embedding_revision: str = "main"
    vncorenlp_dir: Path = Path("/models/vncorenlp")
    data_dir: Path = Path("data")
    daily_quota: int = 30
    history_retention_days: int = 90
    session_ttl_seconds: int = 604800
    query_timeout_seconds: int = 240
    routing_confidence: float = 0.75
    routing_margin: float = 0.15
    support_threshold: float = 0.85
    relevance_threshold: float = 0.55
    provider_timeout_seconds: float = 30
    source_max_bytes: int = 25_000_000
    source_max_pages: int = 100
    source_delay_seconds: float = 1
    source_user_agent: str = "VietnameseLegalRAG/0.1"
    source_list_urls: str = "https://vanban.chinhphu.vn/,https://congbao.chinhphu.vn/"

    @model_validator(mode="after")
    def validate_runtime(self):
        self.public_origin = self.public_origin.rstrip("/")
        if not self.database_url.get_secret_value().startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL requires a PostgreSQL URI")
        if not self.redis_url.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL requires a Redis URI")
        for key in ("session_secret", "embedding_api_key"):
            if len(getattr(self, key).get_secret_value()) < 32:
                raise ValueError(f"{key} must contain at least 32 characters")
        if self.embedding_model != "intfloat/multilingual-e5-large":
            raise ValueError("v1 requires intfloat/multilingual-e5-large")
        # if self.gemini_model != "gemini-3.5-flash-lite":
        #     raise ValueError("v1 requires gemini-3.5-flash-lite; no automatic replacement")
        self.jev_ai_base_url = self.jev_ai_base_url.rstrip("/")
        if self.jev_ai_base_url != "https://jev-ai.pro/api":
            raise ValueError("JEV_AI_BASE_URL must be https://jev-ai.pro/api; no provider fallback")
        if self.jev_ai_model not in ("jev-latest", "jev-1.13.0"):
            raise ValueError("JEV_AI_MODEL must be jev-latest or jev-1.13.0")
        if self.embedding_batch_size < 1 or self.daily_quota < 1:
            raise ValueError("batch size and quota must be positive")
        if self.environment == "production":
            if not self.public_origin.startswith("https://"):
                raise ValueError("production requires HTTPS")
            for key in ("google_client_id", "google_client_secret", "gemini_api_key", "jev_ai_api_key"):
                value = getattr(self, key)
                if not (value.get_secret_value() if isinstance(value, SecretStr) else value):
                    raise ValueError(f"{key} is required in production")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
