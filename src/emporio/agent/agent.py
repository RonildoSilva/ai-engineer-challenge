"""Agente de atendimento: persona + loop de function calling sobre as tools MCP.

Um turno:
1. o modelo recebe a persona, o contexto do momento, o histórico e as tools;
2. se ele pedir tools, o agente as executa via MCP e devolve os resultados;
3. repete até a resposta final (no máximo `max_rounds` rodadas).

Proteções:
- argumento inválido, tool inexistente ou erro da tool viram mensagem para o modelo corrigir;
- LLM fora do ar, limite de rodadas ou resposta vazia → resposta segura de encaminhamento à equipe
  (manual §7.3: reclamações e casos não resolvidos têm retorno em até 24 horas úteis);
- o rastro de cada turno (tools, argumentos, latência) fica registrado, com e-mail/telefone
  mascarados.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Protocol

import anyio

from emporio.agent.llm import ChatResult, LLMUnavailableError, ToolCall
from emporio.agent.mcp_client import McpToolbox
from emporio.log import get_logger

log = get_logger(__name__)

FALLBACK_ANSWER = (
    "Desculpe, não consegui concluir sua solicitação agora. Vou encaminhar sua mensagem para a "
    "equipe da Empório da Música, que retorna em até 24 horas úteis. Se preferir, fale com a loja "
    "pelo WhatsApp (67) 3321-4500."
)
PII_ARGS = {"email_ou_telefone"}


class ChatModel(Protocol):
    """O que o agente precisa do LLM (o `LLMClient` atende; nos testes, um LLM roteirizado)."""

    def chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatResult: ...


@dataclass
class ToolTrace:
    name: str
    arguments: dict[str, Any]
    ok: bool
    output: str
    latency_s: float


@dataclass
class Turn:
    user: str
    answer: str
    tools: list[ToolTrace] = field(default_factory=list)
    llm_calls: int = 0
    latency_s: float = 0.0
    fallback: str | None = None  # motivo, se a resposta for a de segurança


def _masked(args: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k in PII_ARGS else v) for k, v in args.items()}


class Agent:
    def __init__(
        self,
        llm: ChatModel,
        toolbox: McpToolbox,
        *,
        reference_date: date,
        max_rounds: int = 5,
    ):
        self.llm = llm
        self.toolbox = toolbox
        self.reference_date = reference_date
        self.max_rounds = max_rounds
        self.messages: list[dict[str, Any]] = []

    async def start(self, customer_first_name: str | None = None) -> None:
        """Monta o system prompt: persona (prompt MCP) + contexto do momento (tool status_loja)."""
        persona = await self.toolbox.persona(customer_first_name)
        store = await self.toolbox.call("status_loja", {})
        status = store.data.get("resposta", "") if store.data else ""
        context = (
            f"\n\nContexto: a data de referência do sistema é {self.reference_date:%d/%m/%Y} "
            f"(use-a como 'hoje' para prazos). Horário real da loja agora: {status}"
        )
        self.messages = [{"role": "system", "content": persona + context}]

    async def respond(self, user_message: str) -> Turn:
        if not self.messages:
            await self.start()
        turn = Turn(user=user_message, answer="")
        t0 = time.perf_counter()
        self.messages.append({"role": "user", "content": user_message})
        try:
            await self._loop(turn)
        except LLMUnavailableError as e:
            log.warning("LLM indisponível: resposta de segurança", extra={"reason": str(e)})
            turn.fallback = f"LLM indisponível: {e}"
            turn.answer = FALLBACK_ANSWER
        turn.latency_s = time.perf_counter() - t0
        self.messages.append({"role": "assistant", "content": turn.answer})
        log.info(
            "turno concluído",
            extra={
                "tools": [t.name for t in turn.tools],
                "llm_calls": turn.llm_calls,
                "latency_s": round(turn.latency_s, 2),
                "fallback": turn.fallback,
            },
        )
        return turn

    async def _loop(self, turn: Turn) -> None:
        # mensagens desta rodada de tools: não entram no histórico persistente, só no turno
        working = list(self.messages)
        for _ in range(self.max_rounds):
            result: ChatResult = await anyio.to_thread.run_sync(
                lambda: self.llm.chat(working, tools=self.toolbox.tools)
            )
            turn.llm_calls += 1
            if not result.tool_calls:
                if result.content.strip():
                    turn.answer = result.content.strip()
                else:
                    turn.fallback = "o modelo devolveu uma resposta vazia"
                    turn.answer = FALLBACK_ANSWER
                return
            working.append(self._assistant_tool_message(result))
            # sequencial (não paralelo): a ordem das chamadas é preservada no rastro
            working.extend([await self._run_tool(call, turn) for call in result.tool_calls])
        turn.fallback = f"limite de {self.max_rounds} rodadas de ferramentas atingido"
        turn.answer = FALLBACK_ANSWER

    @staticmethod
    def _assistant_tool_message(result: ChatResult) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": result.content or None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.raw_arguments or "{}"},
                }
                for c in result.tool_calls
            ],
        }

    def _resolve_name(self, name: str) -> str | None:
        """Nome exato, ou quase certo ("detalhar_produtoArguments", "detalhar_produtos")."""
        if name in self.toolbox.tool_names:
            return name
        for known in self.toolbox.tool_names:
            if name.startswith(known):
                return known
        close = difflib.get_close_matches(name, self.toolbox.tool_names, n=1, cutoff=0.85)
        return close[0] if close else None

    async def _run_tool(self, call: ToolCall, turn: Turn) -> dict[str, Any]:
        t0 = time.perf_counter()
        if (resolved := self._resolve_name(call.name)) and resolved != call.name:
            log.info("nome de tool corrigido", extra={"from": call.name, "to": resolved})
            call = replace(call, name=resolved)
        if call.error:
            ok, output = False, f"Argumentos inválidos: {call.error}. Envie um objeto JSON válido."
        elif call.name not in self.toolbox.tool_names:
            names = ", ".join(sorted(self.toolbox.tool_names))
            ok, output = False, f"A ferramenta '{call.name}' não existe. Disponíveis: {names}."
        else:
            r = await self.toolbox.call(call.name, call.arguments)
            ok, output = r.ok, r.text
        turn.tools.append(
            ToolTrace(call.name, _masked(call.arguments), ok, output, time.perf_counter() - t0)
        )
        return {"role": "tool", "tool_call_id": call.id, "content": output}

    def reset(self) -> None:
        self.messages = []
