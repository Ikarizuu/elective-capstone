"""Application configuration and environment settings."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# Shipped placeholder secrets. Startup refuses these unless demo mode is explicitly enabled.
DEFAULT_JWT_SECRET = "ai-has-enterprise-dev-secret-change-in-production"
DEFAULT_WEBHOOK_SECRET = "ai-has-webhook-hmac-secret"


def _default_kb_dir() -> str:
    candidates = [
        PROJECT_ROOT / "knowledge_base",
        BACKEND_DIR / "knowledge_base",
        Path("/app/knowledge_base"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(PROJECT_ROOT / "knowledge_base")


class Settings(BaseSettings):
    """Typed runtime configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), str(BACKEND_DIR / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI-HAS Enterprise Helpdesk"
    app_version: str = "1.0.0"

    # OpenAI Configuration
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_chat_model: str = Field(default="gpt-4o-mini", alias="OPENAI_CHAT_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
    )

    # Provider selection: openai, gemini, or local deterministic mode.
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_chat_model: str = Field(default="gemini-3.8-flash", alias="GEMINI_CHAT_MODEL")

    # Database & Storage
    database_url: str = Field(
        default=f"sqlite:///{(BACKEND_DIR / 'data' / 'ai_has.db').as_posix()}",
        alias="DATABASE_URL",
    )
    chroma_persist_dir: str = Field(
        default=str(BACKEND_DIR / "data" / "chroma"), alias="CHROMA_PERSIST_DIR"
    )
    knowledge_base_dir: str = Field(
        default_factory=_default_kb_dir, alias="KNOWLEDGE_BASE_DIR"
    )

    # Auth & RBAC
    jwt_secret: str = Field(default=DEFAULT_JWT_SECRET, alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    # DEMO ONLY: trusts X-User-Role headers and enables the unauthenticated /api/auth/token minter.
    allow_header_role_auth: bool = Field(default=False, alias="ALLOW_HEADER_ROLE_AUTH")

    # Webhooks
    webhook_timeout_seconds: float = Field(default=8.0, alias="WEBHOOK_TIMEOUT_SECONDS")
    jira_webhook_url: str = Field(default="", alias="JIRA_WEBHOOK_URL")
    servicenow_webhook_url: str = Field(default="", alias="SERVICENOW_WEBHOOK_URL")
    n8n_webhook_url: str = Field(default="", alias="N8N_WEBHOOK_URL")
    webhook_signing_secret: str = Field(
        default=DEFAULT_WEBHOOK_SECRET, alias="WEBHOOK_SIGNING_SECRET"
    )
    webhook_max_retries: int = Field(default=2, alias="WEBHOOK_MAX_RETRIES", ge=0, le=6)
    webhook_retry_backoff_seconds: float = Field(
        default=0.5, alias="WEBHOOK_RETRY_BACKOFF_SECONDS", ge=0.0
    )
    jira_project_key: str = Field(default="ITSD", alias="JIRA_PROJECT_KEY")
    # Full `Authorization` header values (e.g. "Basic <b64>" or "Bearer <token>"); never logged.
    jira_authorization: str = Field(default="", alias="JIRA_AUTHORIZATION")
    servicenow_authorization: str = Field(default="", alias="SERVICENOW_AUTHORIZATION")

    # CORS & Logging
    backend_cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="BACKEND_CORS_ORIGINS",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # RAG Tuning
    rag_top_k: int = 5
    rag_chunk_size: int = 750
    rag_chunk_overlap: int = 120
    rag_min_confidence: float = 0.18

    def insecure_default_secrets(self) -> List[str]:
        """Names of secrets still set to the shipped placeholder values."""
        problems: List[str] = []
        if self.jwt_secret == DEFAULT_JWT_SECRET:
            problems.append("JWT_SECRET")
        if self.webhook_signing_secret == DEFAULT_WEBHOOK_SECRET:
            problems.append("WEBHOOK_SIGNING_SECRET")
        return problems

    @property
    def has_openai_key(self) -> bool:
        key = (self.openai_api_key or "").strip()
        if not key:
            return False
        if key in {
            "sk-your-openai-api-key-here",
            "your-openai-api-key",
            "changeme",
            "none",
        }:
            return False
        return len(key) >= 12

    @property
    def has_gemini_key(self) -> bool:
        key = (self.gemini_api_key or "").strip()
        return len(key) >= 12 and key.lower() not in {"your-gemini-api-key", "changeme", "none"}

    @property
    def active_llm_provider(self) -> str:
        provider = self.llm_provider.strip().lower()
        if provider == "gemini" and self.has_gemini_key:
            return "gemini"
        if provider == "openai" and self.has_openai_key:
            return "openai"
        return "local"

    @property
    def cors_origins_list(self) -> List[str]:
        return [
            origin.strip()
            for origin in self.backend_cors_origins.split(",")
            if origin.strip()
        ]

    def resolved_kb_dir(self) -> Path:
        kb_path = Path(self.knowledge_base_dir)
        if not kb_path.is_absolute():
            for base in (BACKEND_DIR, PROJECT_ROOT, Path.cwd()):
                candidate = (base / kb_path).resolve()
                if candidate.exists():
                    return candidate
            kb_path = (BACKEND_DIR / kb_path).resolve()
        kb_path.mkdir(parents=True, exist_ok=True)
        return kb_path

    def resolved_chroma_dir(self) -> Path:
        chroma_path = Path(self.chroma_persist_dir)
        if not chroma_path.is_absolute():
            chroma_path = (BACKEND_DIR / chroma_path).resolve()
        chroma_path.mkdir(parents=True, exist_ok=True)
        return chroma_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
