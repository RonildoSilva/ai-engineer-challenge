"""API REST do atendimento: o mesmo agente da CLI, como serviço HTTP.

É o contrato único dos canais: um webhook de WhatsApp e a interface web falam com a Mel por aqui,
com a mesma `ChatSession` (histórico, guardrails, pré-busca) que o `emporio chat` usa.

    GET  /health                 servidor, perfil e LLM no ar
    POST /chat                   {"mensagem": "...", "sessao": "opcional"} → resposta + rastro
    POST /chat/stream            o mesmo, em Server-Sent Events (assunto, dados, redigindo…)
    GET  /sessions/{id}          histórico da sessão (e-mail e telefone mascarados)

O streaming é POST (lido com `fetch` no navegador), não GET/EventSource: com GET a mensagem iria na
URL e um e-mail ou telefone do cliente acabaria em logs de acesso e de proxies.

Sem estado entre requisições além de um cache de sessões: o histórico fica no `HistoryStore`
(SQLite ou Postgres), então uma sessão pode ser retomada por qualquer réplica.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import OrderedDict
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse as StarletteJSON
from starlette.routing import Route

from emporio.agent.agent import Agent, ChatModel, Turn
from emporio.agent.budget import ContextBudget
from emporio.agent.chat import ChatSession, turn_meta
from emporio.agent.mcp_client import McpToolbox
from emporio.log import get_logger
from emporio.ports import HistoryStore

log = get_logger(__name__)


class JSONResponse(StarletteJSON):
    """JSON em UTF-8 legível: acentos sem escape ("horário", e não a forma com código)."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


MAX_MESSAGE_CHARS = 4000  # acima disso, 413 (o guardrail já recusa acima de 2000, com resposta)
SESSION_CACHE = 256  # sessões com agente em memória; as demais são reabertas do histórico


@dataclass
class ApiConfig:
    profile: str
    model: str
    reference_date: date
    budget: ContextBudget
    history_window: int = 20
    cors_origins: tuple[str, ...] = ()


class ChatService:
    """Sessões de chat sobre um toolbox MCP aberto. Um turno por vez em cada sessão."""

    def __init__(self, llm: ChatModel, toolbox: McpToolbox, store: HistoryStore, cfg: ApiConfig):
        self.llm, self.toolbox, self.store, self.cfg = llm, toolbox, store, cfg
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    async def session(self, session_id: str | None) -> ChatSession:
        if session_id and session_id in self._sessions:
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]
        agent = Agent(self.llm, self.toolbox, reference_date=self.cfg.reference_date,
                      budget=self.cfg.budget)  # fmt: skip
        session = ChatSession(agent, self.store, profile=self.cfg.profile,
                              session_id=session_id, window=self.cfg.history_window)  # fmt: skip
        await session.open()  # sessão inexistente → KeyError
        self._sessions[session.session_id] = session
        while len(self._sessions) > SESSION_CACHE:  # a mais antiga sai; o histórico continua salvo
            old, _ = self._sessions.popitem(last=False)
            self._locks.pop(old, None)
        return session

    async def send(self, session_id: str | None, text: str, on_event=None) -> tuple[str, Turn]:
        session = await self.session(session_id)
        lock = self._locks.setdefault(session.session_id, asyncio.Lock())
        async with lock:  # duas mensagens da mesma sessão ao mesmo tempo: uma de cada vez
            session.agent.on_event = on_event
            try:
                turn = await session.send(text)
            finally:
                session.agent.on_event = None
        return session.session_id, turn


def turn_payload(session_id: str, turn: Turn) -> dict[str, Any]:
    meta = turn_meta(turn)
    return {
        "sessao": session_id,
        "resposta": turn.answer,
        "request_id": turn.request_id,
        "assunto": meta["intents"],
        "pre_busca": meta["prefetched"],
        "tools": meta["tools"],
        "chamadas_llm": turn.llm_calls,
        "prompt_tokens": turn.prompt_tokens,
        "latencia_s": meta["latency_s"],
        "resposta_pronta": turn.llm_calls == 0 and not turn.fallback,
        "bloqueio": turn.guardrail,
        "encaminhado": turn.escalated,
        "resposta_de_seguranca": turn.fallback,
    }


async def _message(request: Request) -> tuple[str | None, str] | JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"erro": "corpo precisa ser JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"erro": "corpo precisa ser um objeto JSON"}, status_code=400)
    text, session_id = body.get("mensagem"), body.get("sessao")
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"erro": "campo 'mensagem' (texto) é obrigatório"}, status_code=400)
    if len(text) > MAX_MESSAGE_CHARS:
        return JSONResponse({"erro": "mensagem longa demais"}, status_code=413)
    if session_id is not None and not isinstance(session_id, str):
        return JSONResponse({"erro": "campo 'sessao' precisa ser texto"}, status_code=400)
    return session_id, text.strip()


def _unknown_session(session_id: str | None) -> JSONResponse:
    return JSONResponse({"erro": f"sessão '{session_id}' não existe"}, status_code=404)


# ------------------------------------------------------------------------------ rotas


async def health(request: Request) -> JSONResponse:
    service: ChatService = request.app.state.service
    ping = getattr(service.llm, "ping", None)
    llm_ok = await asyncio.to_thread(ping) if callable(ping) else True
    return JSONResponse({"status": "ok" if llm_ok else "degradado",
                         "perfil": service.cfg.profile, "modelo": service.cfg.model,
                         "llm": "ok" if llm_ok else "fora do ar"})  # fmt: skip


async def chat(request: Request) -> JSONResponse:
    parsed = await _message(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    session_id, text = parsed
    try:
        sid, turn = await request.app.state.service.send(session_id, text)
    except KeyError:
        return _unknown_session(session_id)
    return JSONResponse(turn_payload(sid, turn))


async def chat_stream(request: Request) -> JSONResponse | EventSourceResponse:
    """Turno em Server-Sent Events, para a interface mostrar o progresso.

    Eventos: `sessao`, `bloqueio`, `assunto`, `dados`, `redigindo`, `tool`, `correcao`,
    `resposta` (o mesmo corpo do `POST /chat`) ou `erro`.
    """
    parsed = await _message(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    session_id, text = parsed
    service: ChatService = request.app.state.service
    try:
        session = await service.session(session_id)
    except KeyError:
        return _unknown_session(session_id)

    async def events() -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        task = asyncio.create_task(
            service.send(session.session_id, text, lambda e, d: queue.put_nowait((e, d)))
        )
        yield {"event": "sessao", "data": _json({"sessao": session.session_id})}
        try:
            while not task.done() or not queue.empty():
                with contextlib.suppress(TimeoutError):
                    event, data = await asyncio.wait_for(queue.get(), timeout=0.25)
                    yield {"event": event, "data": _json(data)}
            sid, turn = task.result()
            yield {"event": "resposta", "data": _json(turn_payload(sid, turn))}
        except Exception as e:  # o cliente recebe um evento de erro, sem detalhes internos
            log.exception("falha no turno via stream")
            yield {"event": "erro", "data": _json({"erro": type(e).__name__})}
        finally:
            if not task.done():  # cliente desconectou: o turno termina e fica no histórico
                await asyncio.shield(task)

    return EventSourceResponse(events(), ping=15)


async def session_history(request: Request) -> JSONResponse:
    service: ChatService = request.app.state.service
    sid = request.path_params["session_id"]
    info = service.store.get_session(sid)
    if info is None:
        return _unknown_session(sid)
    return JSONResponse({
        "sessao": info.id, "perfil": info.profile, "criada_em": info.created_at,
        "atualizada_em": info.updated_at,
        "mensagens": service.store.messages(sid),
        "turnos": service.store.turns_meta(sid),
    })  # fmt: skip


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


# ------------------------------------------------------------------------------ app


def build_app(*, llm: ChatModel, mcp_target: Any, store: HistoryStore, cfg: ApiConfig) -> Starlette:
    """App ASGI da API.

    O toolbox MCP (`mcp_target`: servidor em processo, URL ou stdio) fica aberto durante a vida
    do app.
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        async with McpToolbox(mcp_target) as toolbox:
            app.state.service = ChatService(llm, toolbox, store, cfg)
            log.info("API do atendimento no ar", extra={"profile": cfg.profile})
            yield

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    ]
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/chat", chat, methods=["POST"]),
            Route("/chat/stream", chat_stream, methods=["POST"]),
            Route("/sessions/{session_id}", session_history, methods=["GET"]),
        ],
        middleware=middleware,
        lifespan=lifespan,
    )
