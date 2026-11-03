"""Orçamento de contexto: o que cabe na janela do modelo, por ordem de prioridade.

O MCP publica o custo do que entrega (`tokens_estimados` de cada seção do manual) e o host
decide quanto cabe. A mesma regra serve para uma janela de 8 mil tokens (Bonsai local) e para
128 mil (APIs online):

1. sempre: system prompt (persona + contexto) e a mensagem atual do cliente;
2. reserva para a resposta (`max_output_tokens` do perfil) e margem de erro da estimativa;
3. dados pré-buscados, em ordem de relevância, até a fração `evidence_share` do disponível;
4. definições das tools oferecidas;
5. histórico, da mensagem mais recente para a mais antiga, aos pares (cliente + resposta).

A estimativa é a mesma do pipeline de políticas (caracteres / 4): conservadora para PT-BR.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from emporio.data.policies import CHARS_PER_TOKEN

MESSAGE_OVERHEAD = 4  # tokens de marcação por mensagem no template de chat
SAFETY_MARGIN = 0.05  # a estimativa por caracteres pode errar para menos


def estimate_tokens(value: str | dict[str, Any] | list[Any]) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content") or ""
    calls = message.get("tool_calls") or []
    return MESSAGE_OVERHEAD + estimate_tokens(content) + (estimate_tokens(calls) if calls else 0)


@dataclass(frozen=True)
class ContextBudget:
    window: int  # janela de contexto do modelo (perfil ou /props)
    reserve_output: int  # tokens reservados para a resposta
    evidence_share: float = 0.4  # fração do disponível para os dados pré-buscados

    @property
    def available(self) -> int:
        """Tokens de entrada utilizáveis: janela menos resposta menos margem."""
        return max(0, int(self.window * (1 - SAFETY_MARGIN)) - self.reserve_output)

    @property
    def evidence_limit(self) -> int:
        return int(self.available * self.evidence_share)

    # -------------------------------------------------------------- tamanho da pré-busca

    @property
    def policy_sections(self) -> int:
        """Quantas seções do manual pedir ao MCP (cada uma tem ~130–570 tokens)."""
        return 1 if self.evidence_limit < 1500 else 2 if self.evidence_limit < 6000 else 3  # noqa: PLR2004

    @property
    def product_results(self) -> int:
        """Quantos produtos pedir na busca do catálogo."""
        return 3 if self.evidence_limit < 1500 else 5  # noqa: PLR2004

    def pick_sections(self, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Seções do manual que cabem no limite, usando o custo publicado pelo MCP.

        A primeira (mais relevante) sempre entra; as demais só se couberem inteiras.
        """
        chosen: list[dict[str, Any]] = []
        used = 0
        for s in sections:
            cost = int(s.get("tokens_estimados") or estimate_tokens(s.get("conteudo", "")))
            if chosen and used + cost > self.evidence_limit:
                continue
            chosen.append(s)
            used += cost
        return chosen


@dataclass
class Fitted:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    tokens: int  # estimativa da entrada que vai ao modelo
    dropped_history: int = 0  # mensagens antigas que ficaram de fora
    dropped_evidence: int = 0  # resultados pré-buscados que ficaram de fora
    dropped_tools: list[str] = field(default_factory=list)


def fit(
    budget: ContextBudget,
    *,
    system: dict[str, Any],
    history: list[dict[str, Any]],
    user: dict[str, Any],
    evidence: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> Fitted:
    """Monta as mensagens na ordem do chat, cortando pelo que tem menor prioridade.

    `evidence` são mensagens já no formato do chat (uma de assistant com `tool_calls` seguida
    das respostas `tool`); elas entram depois da mensagem do cliente, como se o modelo as tivesse
    pedido. Cada resultado `tool` sai inteiro ou não sai (nunca truncado no meio de um JSON).
    """
    used = message_tokens(system) + message_tokens(user)
    room = budget.available - used

    kept_evidence, dropped_evidence = _fit_evidence(evidence, min(room, budget.evidence_limit))
    used += sum(message_tokens(m) for m in kept_evidence)

    kept_tools: list[dict[str, Any]] = []
    dropped_tools: list[str] = []
    for t in tools or []:
        cost = estimate_tokens(t)
        if used + cost <= budget.available:
            kept_tools.append(t)
            used += cost
        else:
            dropped_tools.append(t["function"]["name"])

    kept_history: list[dict[str, Any]] = []
    for i in range(len(history) - 1, -1, -2):  # pares (user, assistant), do mais recente
        pair = history[max(0, i - 1) : i + 1]
        cost = sum(message_tokens(m) for m in pair)
        if used + cost > budget.available:
            break
        kept_history[:0] = pair
        used += cost

    return Fitted(
        messages=[system, *kept_history, user, *kept_evidence],
        tools=kept_tools or None,
        tokens=used,
        dropped_history=len(history) - len(kept_history),
        dropped_evidence=dropped_evidence,
        dropped_tools=dropped_tools,
    )


def _fit_evidence(evidence: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int]:
    """Mantém a chamada (assistant) e os resultados `tool` que cabem, em ordem de relevância."""
    if not evidence:
        return [], 0
    call, results = evidence[0], evidence[1:]
    used = message_tokens(call)
    kept: list[dict[str, Any]] = []
    for r in results:
        cost = message_tokens(r)
        if used + cost > limit and kept:
            continue
        if used + cost > limit:  # nem o primeiro cabe: melhor sem dados do que dados cortados
            break
        kept.append(r)
        used += cost
    if not kept:
        return [], len(results)
    ids = {r["tool_call_id"] for r in kept}
    trimmed_call = {**call, "tool_calls": [c for c in call["tool_calls"] if c["id"] in ids]}
    return [trimmed_call, *kept], len(results) - len(kept)
