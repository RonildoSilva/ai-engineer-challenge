"""Índice vetorial em SQLite: vetores float32 em BLOB, busca por cosseno em memória (numpy).

Com dezenas a poucos milhares de documentos, a busca exata em memória leva microssegundos e
dispensa um banco vetorial. Para volume maior, o mesmo papel é cumprido pelo adaptador pgvector.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE vectors (
    doc_id     TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('product', 'policy')),
    title      TEXT NOT NULL,
    text_hash  TEXT NOT NULL,
    vec        BLOB NOT NULL
);
"""


def write_index(
    path: Path,
    rows: Sequence[tuple[str, str, str, str, Sequence[float] | np.ndarray]],
    meta: dict[str, str],
) -> None:
    """Grava (doc_id, kind, title, text_hash, vetor) de forma atômica; vetores normalizados."""
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        with con:
            con.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))
            con.executemany(
                "INSERT INTO vectors VALUES (?, ?, ?, ?, ?)",
                [(d, k, t, h, _unit(v).tobytes()) for d, k, t, h, v in rows],
            )
    finally:
        con.close()
    tmp.replace(path)


def read_cache(path: Path, model_id: str) -> dict[str, np.ndarray]:
    """Vetores já calculados (por hash do texto), se o índice existente for do mesmo modelo."""
    if not path.is_file():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("model_id") != model_id:
            return {}
        return {
            h: np.frombuffer(v, dtype=np.float32)
            for h, v in con.execute("SELECT text_hash, vec FROM vectors")
        }
    finally:
        con.close()


def _unit(v: Sequence[float] | np.ndarray) -> np.ndarray:
    arr = np.asarray(v, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    return arr / norm if norm else arr


class SqliteVectorIndex:
    """Índice somente leitura, carregado inteiro em memória."""

    def __init__(self, path: Path):
        if not path.is_file():
            raise FileNotFoundError(f"{path} não existe: rode `emporio pipeline run`")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            self.meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            rows = con.execute("SELECT doc_id, kind, title, vec FROM vectors ORDER BY doc_id")
            rows = rows.fetchall()
        finally:
            con.close()
        self.ids = [r[0] for r in rows]
        self.kinds = np.array([r[1] for r in rows])
        self.titles = {r[0]: r[2] for r in rows}
        self._kind_of = {r[0]: r[1] for r in rows}
        self.matrix = (
            np.vstack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
            if rows
            else np.zeros((0, 0), dtype=np.float32)
        )

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
        """(doc_id, similaridade de cosseno), da maior para a menor."""
        if not self.ids:
            return []
        sims = self.matrix @ _unit(query_vec)
        if kind is not None:
            sims = np.where(self.kinds == kind, sims, -np.inf)
        order = np.argsort(-sims, kind="stable")[:k]
        return [(self.ids[i], float(sims[i])) for i in order if np.isfinite(sims[i])]
