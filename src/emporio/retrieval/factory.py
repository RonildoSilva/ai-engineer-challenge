"""Monta embedder e retriever a partir da configuração (único lugar que conhece os adaptadores)."""

from __future__ import annotations

from emporio.adapters.embedders import HashingEmbedder, OpenAICompatibleEmbedder
from emporio.adapters.retrievers import HybridRetriever, LexicalRetriever
from emporio.adapters.vector_sqlite import SqliteVectorIndex
from emporio.config import Settings
from emporio.domain.policies import PolicyStore
from emporio.ports import Embedder, Retriever
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


def build_retriever(settings: Settings, embedding_url: str | None = None) -> Retriever:
    docs = build_documents(settings.store_db, settings.policies_json)
    lexical = LexicalRetriever(docs, PolicyStore.from_json(settings.policies_json))
    embedder = build_embedder(settings, embedding_url)
    if embedder is None or not settings.vectors_db.is_file():
        return lexical
    return HybridRetriever(
        lexical,
        SqliteVectorIndex(settings.vectors_db),
        embedder,
        min_similarity=settings.retrieval_min_similarity,
    )
