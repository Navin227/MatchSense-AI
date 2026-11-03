from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    sportmonks_api_token: str = ""
    sportmonks_base_url: str = "https://cricket.sportmonks.com/api/v2.0"
    poll_interval_seconds: int = Field(default=30, ge=5, le=3600)
    replay_base_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    llm_provider: Literal["ollama", "cerebras", "gemini", "groq"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    cerebras_api_key: str = ""
    cerebras_model: str = "qwen-3.8-27b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = Field(default=25.0, gt=0, le=180)
    llm_retry_attempts: int = Field(default=2, ge=1, le=4)
    llm_max_output_tokens: int = Field(default=1200, ge=64, le=2048)
    llm_cache_ttl_seconds: int = Field(default=120, ge=0, le=3600)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
