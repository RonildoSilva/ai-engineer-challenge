"""Monta embedder e retriever a partir da configuração (único lugar que conhece os adaptadores)."""

from __future__ import annotations

from emporio.adapters.embedders import HashingEmbedder, OpenAICompatibleEmbedder
from emporio.adapters.retrievers import HybridRetriever, LexicalRetriever
from emporio.adapters.sqlite_repo import SqliteRepository
from emporio.adapters.vector_sqlite import SqliteVectorIndex
from emporio.config import Settings
from emporio.domain.policies import PolicyStore
from emporio.ports import Embedder, Repository, Retriever
from emporio.retrieval.documents import build_documents


def build_embedder(settings: Settings, base_url: str | None = None) -> Embedder | None:
    """`base_url` sobrescreve a URL configurada (ex.: servidor iniciado numa porta livre)."""
    if settings.embedding_backend == "none":
        return None
    if settings.embedding_backend == "fake":
        return HashingEmbedder()
    return OpenAICompatibleEmbedder(
        base_url or settings.embedding_base_url,
        settings.embedding_model,
        query_format=settings.embedding_query_format,
        doc_format=settings.embedding_doc_format,
        timeout_s=settings.embedding_timeout_s,
        batch_size=settings.embedding_batch_size,
    )


def build_repository(settings: Settings) -> Repository:
    """SQLite (arquivo do pipeline, padrão) ou Postgres (perfil de escala)."""
    if settings.store_backend == "postgres":
        from emporio.adapters.postgres_repo import PostgresRepository  # noqa: PLC0415 (extra)

        return PostgresRepository(settings.database_url or "")
    return SqliteRepository(settings.store_db)


def build_vector_index(settings: Settings):
    """Índice vetorial: SQLite em memória (padrão) ou pgvector. None se não houver índice."""
    if settings.vector_backend == "pgvector":
        from emporio.adapters.pgvector import PgVectorIndex  # noqa: PLC0415 (extra)

        return PgVectorIndex(settings.database_url or "")
    return SqliteVectorIndex(settings.vectors_db) if settings.vectors_db.is_file() else None


def build_retriever(
    settings: Settings, embedding_url: str | None = None, repo: Repository | None = None
) -> Retriever:
    docs = build_documents(repo or build_repository(settings), settings.policies_json)
    lexical = LexicalRetriever(docs, PolicyStore.from_json(settings.policies_json))
    embedder = build_embedder(settings, embedding_url)
    if embedder is None:
        return lexical
    index = build_vector_index(settings)
    if index is None:
        return lexical
    return HybridRetriever(
        lexical, index, embedder, min_similarity=settings.retrieval_min_similarity
    )
