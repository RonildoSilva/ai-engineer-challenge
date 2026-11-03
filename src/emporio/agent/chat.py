"""Sessão de chat: agente + histórico persistente + transcrição, sem depender da interface.

A CLI (`emporio chat`) usa esta classe; a API REST e a interface web (planejadas) usam a mesma.

- `open()` monta a persona e, se a sessão já existir, retoma as últimas mensagens (janela) e o
  assunto da última pergunta (para "é o pedido 12" continuar o assunto certo);
- `send()` responde e grava a pergunta e a resposta, com os metadados do turno;
- a transcrição opcional (markdown) registra cada turno com as tools usadas: é como as conversas
  de exemplo do projeto são geradas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from emporio.agent.agent import Agent, Turn
from emporio.agent.history import HistoryStore, mask_pii
from emporio.agent.router import route


def turn_meta(turn: Turn) -> dict[str, Any]:
    """O que fica registrado de cada turno (sem dados pessoais nos argumentos)."""
    return {
        "intents": [i.value for i in turn.route.intents] if turn.route else [],
        "prefetched": [t.name for t in turn.prefetched],
        "tools": [t.name for t in turn.tools],
        "llm_calls": turn.llm_calls,
        "prompt_tokens": turn.prompt_tokens,
        "latency_s": round(turn.latency_s, 2),
        "fallback": turn.fallback,
        "guardrail": turn.guardrail,
        "escalated": turn.escalated,
        "redacted": len(turn.redacted),
    }


@dataclass
class Transcript:
    """Conversa em markdown, gravada a cada turno (sobrevive a uma interrupção)."""

    path: Path
    profile: str
    model: str
    session_id: str
    started: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            f"# Conversa com a Mel (Empório da Música)\n\n"
            f"- Sessão: `{self.session_id}`\n"
            f"- Modelo: `{self.model}` (perfil `{self.profile}`)\n"
            f"- Data: {self.started:%Y-%m-%d %H:%M}\n\n---\n",
            encoding="utf-8",
        )

    def add(self, turn: Turn) -> None:
        used = [f"{t.name} (pré-busca)" for t in turn.prefetched] + [t.name for t in turn.tools]
        tools = ", ".join(f"`{u}`" for u in used) or "nenhuma"
        note = f"; resposta de segurança: {turn.fallback}" if turn.fallback else ""
        with self.path.open("a", encoding="utf-8") as f:
            f.write(
                f"\n**Cliente:** {mask_pii(turn.user)}\n\n"
                f"**Mel:** {turn.answer}\n\n"
                f"<sub>tools: {tools} · {turn.llm_calls} chamada(s) ao LLM · "
                f"{turn.latency_s:.0f} s{note}</sub>\n"
            )


class ChatSession:
    def __init__(
        self,
        agent: Agent,
        store: HistoryStore,
        *,
        profile: str,
        session_id: str | None = None,
        window: int = 20,
        transcript: Transcript | None = None,
    ):
        self.agent = agent
        self.store = store
        self.profile = profile
        self.window = window
        self.transcript = transcript
        self.session_id = session_id or store.create_session(profile)
        self.resumed = 0  # mensagens retomadas do histórico

    async def open(self) -> None:
        if self.store.get_session(self.session_id) is None:
            raise KeyError(f"sessão '{self.session_id}' não existe")
        await self.agent.start()
        past = self.store.messages(self.session_id, window=self.window)
        self.agent.messages += past
        self.resumed = len(past)
        last_user = next((m["content"] for m in reversed(past) if m["role"] == "user"), None)
        if last_user:
            self.agent.last_route = route(last_user)

    async def send(self, text: str) -> Turn:
        turn = await self.agent.respond(text)
        self.store.append(self.session_id, "user", text)
        self.store.append(self.session_id, "assistant", turn.answer, turn_meta(turn))
        if self.transcript:
            self.transcript.add(turn)
        return turn

    async def new(self) -> None:
        """Começa outra sessão (comando /nova)."""
        self.session_id = self.store.create_session(self.profile)
        self.agent.reset()
        await self.open()
