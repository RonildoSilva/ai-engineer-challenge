"""Configuração central, validada na inicialização (falha cedo, com mensagem clara).

Fontes, em ordem de prioridade: variáveis de ambiente > arquivo `.env` na raiz > defaults.
Caminhos relativos são resolvidos a partir da raiz do projeto.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
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
    data_curated_dir: Path = Path("data/curated")  # revisões manuais versionadas

    # --- regras de negócio ----------------------------------------------------------
    # "Hoje" das regras de prazo: os pedidos do dataset vão até 2026-03-22.
    reference_date: date = date(2026, 3, 25)

    # --- logs -----------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    # --- LLM ------------------------------------------------------------------------
    # Perfil ativo em config/llm_profiles.yaml (None = o perfil `default` do arquivo).
    llm_profile: str | None = None
    llm_profiles_file: Path = Path("config/llm_profiles.yaml")
    # Sobrescritas opcionais do perfil ativo (ex.: apontar o bonsai para outra porta).
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    # --- embeddings e busca ----------------------------------------------------------
    # llamafile: servidor local (sobe sozinho se o modelo estiver baixado); fake: determinístico,
    # para testes; none: só busca lexical.
    embedding_backend: Literal["llamafile", "fake", "none"] = "llamafile"
    embedding_base_url: str = "http://127.0.0.1:8081/v1"
    embedding_model: str = "embeddinggemma"
    embedding_model_file: Path = Path("models/embeddinggemma-300m/embeddinggemma-300M-Q8_0.gguf")
    # formatos recomendados pelo EmbeddingGemma para busca (consulta × documento)
    embedding_query_format: str = "task: search result | query: {text}"
    embedding_doc_format: str = "title: {title} | text: {text}"
    embedding_timeout_s: float = Field(default=60.0, gt=0)
    embedding_batch_size: int = Field(default=8, ge=1, le=64)
    # similaridade mínima (cosseno) para um resultado vetorial contar. Calibrada com o
    # embeddinggemma: resposta certa ≥ 0,313; fora de escopo ≤ 0,292 (docs/embeddings_eval.md)
    retrieval_min_similarity: float = Field(default=0.30, ge=0.0, le=1.0)

    # --- backends de dados (perfil de escala) -------------------------------------------
    # sqlite: arquivos do pipeline (padrão, sem servidor). postgres/pgvector: o mesmo conteúdo
    # carregado por `emporio db load` (requer `uv sync --extra scale` e DATABASE_URL).
    store_backend: Literal["sqlite", "postgres"] = "sqlite"
    vector_backend: Literal["sqlite", "pgvector"] = "sqlite"
    database_url: str | None = None  # ex.: postgresql://emporio:emporio@127.0.0.1:5432/emporio
    history_backend: Literal["sqlite", "postgres"] = "sqlite"  # histórico de conversas

    @model_validator(mode="after")
    def _database_url_required(self) -> Settings:
        uses_pg = (
            self.store_backend == "postgres"
            or self.vector_backend == "pgvector"
            or self.history_backend == "postgres"
        )
        if uses_pg and not self.database_url:
            raise ValueError(
                "STORE_BACKEND=postgres ou VECTOR_BACKEND=pgvector exigem DATABASE_URL"
            )
        return self

    # --- API REST (`emporio api`) ------------------------------------------------------------
    # origens que o navegador pode usar para chamar a API (a interface web roda na 8020)
    api_cors_origins: list[str] = ["http://127.0.0.1:8020", "http://localhost:8020"]

    # --- histórico de conversas --------------------------------------------------------
    # mensagens retomadas ao continuar uma sessão (o orçamento de contexto ainda corta o excesso)
    history_window: int = Field(default=20, ge=0)

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
    def policies_curated_dir(self) -> Path:
        return self.path(self.data_curated_dir) / "politicas"

    @property
    def policies_json(self) -> Path:
        return self.processed_dir / "policies.json"

    @property
    def vectors_db(self) -> Path:
        return self.processed_dir / "vectors.db"

    @property
    def store_db(self) -> Path:
        return self.processed_dir / "store.db"

    @property
    def history_db(self) -> Path:
        return self.processed_dir / "history.db"

    @property
    def pipeline_state_file(self) -> Path:
        return self.processed_dir / "manifest.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
