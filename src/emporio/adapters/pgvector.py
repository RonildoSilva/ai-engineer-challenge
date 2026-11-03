"""Índice vetorial em Postgres + pgvector (perfil de escala).

Mesmo papel e mesma interface do `SqliteVectorIndex` (`search`, `model_id`, `kind_of`, `titles`,
`__len__`): o `HybridRetriever` não sabe qual dos dois está usando. A diferença é onde a busca
acontece: aqui, no banco, com índice HNSW de cosseno, o que sustenta milhões de vetores e várias
réplicas do servidor MCP lendo o mesmo índice.

Requer o extra `scale` (`uv sync --extra scale`).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from emporio.adapters.postgres_repo import connect_read_only


def _literal(vec: Sequence[float] | np.ndarray) -> str:
    """Vetor no formato de texto do pgvector: '[0.1,0.2,…]' (sem depender do pacote pgvector)."""
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


def load_vectors(con: Any, vectors_db: Path) -> int:
    """Copia o vectors.db do pipeline para as tabelas `vectors`/`vectors_meta` e cria o HNSW.

    Roda dentro da transação de `load_from_pipeline`. Índice vazio (modelo não baixado) é carregado
    vazio: o retriever híbrido degrada para lexical, como no SQLite.
    """
    src = sqlite3.connect(f"file:{vectors_db}?mode=ro", uri=True)
    try:
        meta = src.execute("SELECT key, value FROM meta").fetchall()
        rows = src.execute("SELECT doc_id, kind, title, text_hash, vec FROM vectors").fetchall()
    finally:
        src.close()
    with con.cursor() as cur:
        cur.executemany("INSERT INTO vectors_meta (key, value) VALUES (%s, %s)", meta)
        if not rows:
            return 0
        dim = len(np.frombuffer(rows[0][4], dtype=np.float32))
        cur.execute(f"ALTER TABLE vectors ALTER COLUMN vec TYPE vector({int(dim)})")
        cur.executemany(
            "INSERT INTO vectors (doc_id, kind, title, text_hash, vec) "
            "VALUES (%s, %s, %s, %s, %s::vector)",
            [(d, k, t, h, _literal(np.frombuffer(v, dtype=np.float32))) for d, k, t, h, v in rows],
        )
        cur.execute("CREATE INDEX vectors_hnsw ON vectors USING hnsw (vec vector_cosine_ops)")
    return len(rows)


class PgVectorIndex:
    """Leitura do índice vetorial no Postgres. Uma conexão por instância (somente leitura)."""

    def __init__(self, dsn: str):
        self._con = connect_read_only(dsn)
        self.meta = dict(self._con.execute("SELECT key, value FROM vectors_meta").fetchall())
        rows = self._con.execute("SELECT doc_id, kind, title FROM vectors ORDER BY doc_id")
        rows = rows.fetchall()
        self.ids = [r[0] for r in rows]
        self.titles = {r[0]: r[2] for r in rows}
        self._kind_of = {r[0]: r[1] for r in rows}

    @property
    def model_id(self) -> str:
        return self.meta.get("model_id", "")

    def __len__(self) -> int:
        return len(self.ids)

    def kind_of(self, doc_id: str) -> str:
        return self._kind_of[doc_id]

    def search(
        self, query_vec: Sequence[float], *, k: int, kind: str | None = None
    ) -> list[tuple[str, float]]:
        """(doc_id, similaridade de cosseno), da maior para a menor (`<=>` = distância)."""
        if not self.ids:
            return []
        q = _literal(query_vec)
        sql = "SELECT doc_id, 1 - (vec <=> %s::vector) AS sim FROM vectors"
        params: list[Any] = [q]
        if kind is not None:
            sql += " WHERE kind = %s"
            params.append(kind)
        sql += " ORDER BY vec <=> %s::vector, doc_id LIMIT %s"
        params += [q, k]
        return [(d, float(s)) for d, s in self._con.execute(sql, params).fetchall()]

    def close(self) -> None:
        self._con.close()
