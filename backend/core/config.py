"""Application configuration, loaded from environment variables.

See docs/development/environment.md for what every setting means and
which phase actually requires it to be set.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_debug: bool = True

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    database_url: str = "postgresql://recoverai:change-me@localhost:5432/recoverai"
    redis_url: str = "redis://localhost:6379/0"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Phase 12A-12C — NVIDIA NIM, explanation-only LLM layer. Never a
    # decision maker — see backend/services/explanation.py. Default model
    # is openai/gpt-oss-20b (Phase 12C selection: reliable, ~10-14s
    # observed latency, correct structured JSON on this account's NVIDIA
    # NIM entitlements — moonshotai/kimi-k3, tried in Phase 12B, was
    # measured at 100s+ and unsuitable for a live demo). Configurable via
    # NVIDIA_NIM_MODEL; never hardcoded elsewhere.
    nvidia_nim_api_key: str = ""
    nvidia_nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_nim_model: str = "openai/gpt-oss-20b"
    # 25s: observed live latency was 9-13s typically, one outlier at ~20s —
    # this leaves headroom without making the optional demo button feel stuck.
    llm_request_timeout_seconds: float = 25.0

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    enable_voice_recovery: bool = False
    enable_whatsapp_recovery: bool = False
    enable_llm_explanations: bool = True


settings = Settings()
