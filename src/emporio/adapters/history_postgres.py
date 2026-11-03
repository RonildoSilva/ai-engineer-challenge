"""Histórico de conversas em Postgres (perfil de escala): `HISTORY_BACKEND=postgres`.

Mesmo contrato e mesmas tabelas do SQLite (`tests/contract/history_contract.py`), com o mesmo
mascaramento de e-mail e telefone. Com o histórico no banco, várias réplicas da API (commit 20)
atendem a mesma sessão. As tabelas são criadas se não existirem; `emporio db load` não as apaga.

Requer o extra `scale` (`uv sync --extra scale`).
"""

from __future__ import annotations

import json
import secrets
import threading
from typing import Any

from emporio.adapters.postgres_repo import _psycopg
from emporio.agent.history import _now, _without_blocked, mask_pii
from emporio.ports import SessionInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    profile     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_session ON messages(session_id, id);
"""

_SESSIONS_SQL = (
    "SELECT s.id, s.profile, s.created_at, s.updated_at, COUNT(m.id) "
    "FROM sessions s LEFT JOIN messages m ON m.session_id = s.id "
    "{where} GROUP BY s.id ORDER BY s.updated_at DESC, s.id LIMIT %s"
)


class PostgresHistoryStore:
    """Uma conexão por thread, em autocommit (cada escrita é uma transação curta)."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._local = threading.local()
        self._conn().execute(SCHEMA)

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            psycopg, _ = _psycopg()
            conn = psycopg.connect(self._dsn, autocommit=True)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        if (conn := getattr(self._local, "conn", None)) is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------ sessões

    def create_session(self, profile: str) -> str:
        sid = secrets.token_hex(4)
        now = _now()
        self._conn().execute(
            "INSERT INTO sessions (id, profile, created_at, updated_at) VALUES (%s, %s, %s, %s)",
            (sid, profile, now, now),
        )
        return sid

    def get_session(self, session_id: str) -> SessionInfo | None:
        sql = _SESSIONS_SQL.format(where="WHERE s.id = %s")
        row = self._conn().execute(sql, (session_id, 1)).fetchone()
        return SessionInfo(*row) if row else None

    def sessions(self, limit: int = 20) -> list[SessionInfo]:
        rows = self._conn().execute(_SESSIONS_SQL.format(where=""), (limit,)).fetchall()
        return [SessionInfo(*r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        self._conn().execute("DELETE FROM sessions WHERE id = %s", (session_id,))

    # ------------------------------------------------------------------ mensagens

    def append(
        self, session_id: str, role: str, content: str, meta: dict[str, Any] | None = None
    ) -> None:
        now = _now()
        with self._conn().transaction():
            self._conn().execute(
                "INSERT INTO messages (session_id, role, content, meta, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (session_id, role, mask_pii(content),
                 json.dumps(meta or {}, ensure_ascii=False), now),
            )  # fmt: skip
            self._conn().execute(
                "UPDATE sessions SET updated_at = %s WHERE id = %s", (now, session_id)
            )

    def messages(
        self, session_id: str, window: int | None = None, *, skip_blocked: bool = False
    ) -> list[dict[str, Any]]:
        full = (
            self._conn()
            .execute(
                "SELECT role, content, meta FROM messages WHERE session_id = %s ORDER BY id",
                (session_id,),
            )
            .fetchall()
        )
        rows = [(r, c) for r, c, _ in (_without_blocked(full) if skip_blocked else full)]
        if window is not None:
            rows = rows[-window:] if window > 0 else []
            while rows and rows[0][0] != "user":
                rows = rows[1:]
        return [{"role": r, "content": c} for r, c in rows]

    def turns_meta(self, session_id: str) -> list[dict[str, Any]]:
        rows = (
            self._conn()
            .execute(
                "SELECT meta FROM messages WHERE session_id = %s AND role = 'assistant' "
                "ORDER BY id",
                (session_id,),
            )
            .fetchall()
        )
        return [json.loads(m) for (m,) in rows]
