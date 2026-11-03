"""Histórico de conversas em SQLite: sessões, mensagens e metadados de cada turno.

- Uma sessão é uma conversa (id curto, perfil de LLM, datas).
- Cada mensagem guarda papel (user/assistant), texto e metadados do turno da resposta (assunto,
  tools, chamadas ao LLM, latência, resposta de segurança), úteis para auditoria e avaliação.
- **Privacidade (LGPD):** e-mails e telefones são mascarados antes de gravar. Ao retomar uma
  sessão, o cliente informa o contato de novo para consultar pedidos, como num atendimento novo.
- A janela de retomada limita quantas mensagens voltam ao agente; o orçamento de contexto ainda
  corta o que não couber no modelo.
- Mesmo motor do banco da loja (SQLite, sem dependência nova); o esquema é SQL simples, portável
  para o Postgres do perfil de escala.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from emporio.config import Settings
from emporio.ports import HistoryStore, SessionInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    profile     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_session ON messages(session_id, id);
"""

# `where` é um de dois textos fixos deste módulo; valores sempre por parâmetro
_SESSIONS_SQL = (
    "SELECT s.id, s.profile, s.created_at, s.updated_at, COUNT(m.id) "
    "FROM sessions s LEFT JOIN messages m ON m.session_id = s.id "
    "{where} GROUP BY s.id ORDER BY s.updated_at DESC, s.rowid DESC LIMIT ?"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# telefones com 8+ dígitos (com ou sem DDD/+55, separadores comuns)
_PHONE = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[\s.-]?\d{4}\b")


def mask_pii(text: str) -> str:
    """E-mail → [e-mail], telefone → [telefone]. Números curtos (pedido, preço) ficam."""
    return _PHONE.sub("[telefone]", _EMAIL.sub("[e-mail]", text))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class SqliteHistoryStore:
    """Histórico em SQLite (padrão).

    Uma conexão por thread (o agente roda o LLM em outra thread).
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path)
            conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        if conn := getattr(self._local, "conn", None):
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------ sessões

    def create_session(self, profile: str) -> str:
        sid = secrets.token_hex(4)
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions (id, profile, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (sid, profile, now, now),
            )
        return sid

    def get_session(self, session_id: str) -> SessionInfo | None:
        row = self._conn().execute(_SESSIONS_SQL.format(where="WHERE s.id = ?"),
                                   (session_id, 1)).fetchone()  # fmt: skip
        return SessionInfo(*row) if row else None

    def sessions(self, limit: int = 20) -> list[SessionInfo]:
        """Sessões mais recentes primeiro."""
        rows = self._conn().execute(_SESSIONS_SQL.format(where=""), (limit,)).fetchall()
        return [SessionInfo(*r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # ------------------------------------------------------------------ mensagens

    def append(
        self, session_id: str, role: str, content: str, meta: dict[str, Any] | None = None
    ) -> None:
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO messages (session_id, role, content, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, mask_pii(content),
                 json.dumps(meta or {}, ensure_ascii=False), now),
            )  # fmt: skip
            c.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def messages(
        self, session_id: str, window: int | None = None, *, skip_blocked: bool = False
    ) -> list[dict[str, Any]]:
        """Mensagens no formato do chat (`role`, `content`), das mais antigas às mais recentes.

        `window`: só as últimas N, começando sempre por uma mensagem do cliente.
        `skip_blocked`: sem os pares (pergunta, recusa) bloqueados por guardrail, que não voltam
        ao contexto do modelo ao retomar uma sessão.
        """
        full = (
            self._conn()
            .execute(
                "SELECT role, content, meta FROM messages WHERE session_id = ? ORDER BY id",
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
                "SELECT meta FROM messages WHERE session_id = ? AND role = 'assistant' ORDER BY id",
                (session_id,),
            )
            .fetchall()
        )
        return [json.loads(m) for (m,) in rows]


def _without_blocked(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Remove a pergunta e a resposta de cada turno cuja resposta foi um bloqueio de guardrail."""
    out: list[tuple[str, str, str]] = []
    for row in rows:
        if row[0] == "assistant" and json.loads(row[2] or "{}").get("guardrail"):
            if out and out[-1][0] == "user":
                out.pop()
            continue
        out.append(row)
    return out


def build_history_store(settings: Settings) -> HistoryStore:
    """SQLite em data/processed/history.db (padrão) ou Postgres (`HISTORY_BACKEND=postgres`)."""
    if settings.history_backend == "postgres":
        from emporio.adapters.history_postgres import PostgresHistoryStore  # noqa: PLC0415 (extra)

        return PostgresHistoryStore(settings.database_url or "")
    return SqliteHistoryStore(settings.history_db)
