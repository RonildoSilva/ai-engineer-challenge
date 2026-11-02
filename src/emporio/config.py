"""Configuração central, validada na inicialização (falha cedo, com mensagem clara).

Fontes, em ordem de prioridade: variáveis de ambiente > arquivo `.env` na raiz > defaults.
Caminhos relativos são resolvidos a partir da raiz do projeto.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- caminhos -------------------------------------------------------------------
    project_root: Path = PROJECT_ROOT
    data_raw_dir: Path = Path("data/raw")
    data_processed_dir: Path = Path("data/processed")

    # --- regras de negócio ----------------------------------------------------------
    # "Hoje" das regras de prazo: os pedidos do dataset vão até 2026-03-22.
    reference_date: date = date(2026, 3, 25)

    # --- logs -----------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    # --- LLM (perfis completos chegam no commit de perfis) ---------------------------
    llm_base_url: str = "http://localhost:8080/v1"
    llm_model: str = "bonsai"
    llm_api_key: str = "none"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    def path(self, p: Path) -> Path:
        """Resolve um caminho relativo à raiz do projeto."""
        return p if p.is_absolute() else self.project_root / p

    @property
    def raw_dir(self) -> Path:
        return self.path(self.data_raw_dir)

    @property
    def processed_dir(self) -> Path:
        return self.path(self.data_processed_dir)

    @property
    def store_db(self) -> Path:
        return self.processed_dir / "store.db"

    @property
    def pipeline_state_file(self) -> Path:
        return self.processed_dir / "manifest.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
