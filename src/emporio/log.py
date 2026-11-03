"""Logs estruturados: texto legível no terminal ou JSON (uma linha por evento) para coleta.

Uso:
    log = get_logger(__name__)
    log.info("etapa concluída", extra={"stage": "tables", "duration_s": 0.4})

Campos passados em `extra` aparecem como `chave=valor` (texto) ou como chaves do JSON.

Id de requisição: dentro de `with request_context():` (um turno do agente, uma chamada de tool no
servidor MCP), todo log ganha o campo `request_id`, inclusive os emitidos em threads de trabalho
(o contexto é copiado). Com ele, os eventos de um mesmo atendimento são encontrados juntos.
"""

from __future__ import annotations

import json
import logging
import secrets
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def request_context(request_id: str | None = None, prefix: str = "") -> Iterator[str]:
    """Define o id de requisição dos logs dentro do bloco (novo, se não for informado)."""
    rid = request_id or f"{prefix}{secrets.token_hex(6)}"
    token = _request_id.set(rid)
    try:
        yield rid
    finally:
        _request_id.reset(token)


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {k: v for k, v in vars(record).items() if k not in _RESERVED}


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC).strftime("%H:%M:%S")
        extras = " ".join(f"{k}={v}" for k, v in _extras(record).items())
        rid = f" [{r}]" if (r := current_request_id()) else ""
        line = f"{ts} {record.levelname:<7}{rid} {record.name}: {record.getMessage()}"
        if extras:
            line += f"  [{extras}]"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **({"request_id": r} if (r := current_request_id()) else {}),
            **_extras(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger("emporio")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("emporio") else f"emporio.{name}")
