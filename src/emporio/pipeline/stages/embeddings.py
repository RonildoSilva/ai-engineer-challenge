"""Etapa `embed_index`: documentos de busca → vetores → `vectors.db`.

- **Incremental por documento:** vetores de textos que não mudaram (mesmo hash e mesmo modelo)
  são reaproveitados do índice anterior; só o que mudou vai ao modelo.
- **Um comando:** se o servidor de embeddings não estiver de pé, a etapa o sobe pelo
  `start_llamafile.sh` e o encerra ao final.
- **Degradação graciosa:** sem modelo baixado (`SKIP_MODEL=1`) ou com `EMBEDDING_BACKEND=none`,
  grava um índice vazio e a busca funciona só com o lexical. O arquivo do modelo é entrada da
  etapa: quando ele aparecer, a etapa refaz o índice sozinha.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from emporio.adapters.vector_sqlite import read_cache, write_index
from emporio.config import Settings
from emporio.log import get_logger
from emporio.pipeline.core import StageContext
from emporio.ports import EmbedderUnavailableError
from emporio.retrieval.documents import build_documents, text_hash
from emporio.retrieval.factory import build_embedder
from emporio.services import ServerUnavailableError, ensure_server

log = get_logger(__name__)


def embed_index(ctx: StageContext) -> dict[str, Any]:
    settings: Settings = ctx.settings
    out = ctx.outputs[0]
    docs = build_documents(settings.store_db, settings.policies_json)
    embedder = build_embedder(settings)

    def degraded(reason: str) -> dict[str, Any]:
        log.warning("índice vetorial vazio: busca só lexical", extra={"reason": reason})
        write_index(out, [], {"model_id": "none", "reason": reason, "built_at": _now()})
        return {"documents": len(docs), "vectors": 0, "degraded": reason}

    if embedder is None:
        return degraded("EMBEDDING_BACKEND=none")

    cache = read_cache(out, embedder.model_id)
    hashes = [text_hash(d) for d in docs]
    missing = [d for d, h in zip(docs, hashes, strict=True) if h not in cache]

    new: list[list[float]] = []
    if missing:
        try:
            if settings.embedding_backend == "llamafile":
                with ensure_server(
                    "embedding",
                    settings.embedding_base_url,
                    settings.path(settings.embedding_model_file),
                    log_dir=settings.processed_dir / "logs",
                ) as url:
                    new = build_embedder(settings, url).embed_documents(missing)  # type: ignore[union-attr]
            else:
                new = embedder.embed_documents(missing)
        except (ServerUnavailableError, EmbedderUnavailableError) as e:
            return degraded(str(e))

    fresh = dict(zip((text_hash(d) for d in missing), new, strict=True))
    rows = [
        (d.id, d.kind, d.title, h, cache[h] if h in cache else fresh[h])
        for d, h in zip(docs, hashes, strict=True)
    ]
    write_index(out, rows, {"model_id": embedder.model_id, "built_at": _now()})
    return {
        "documents": len(docs),
        "embedded": len(missing),
        "reused": len(docs) - len(missing),
        "dim": len(rows[0][4]) if rows else 0,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()
