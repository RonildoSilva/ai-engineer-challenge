"""Cenários de ponta a ponta: o agente completo (assunto → pré-busca → LLM → guardrails).

Cada cenário é uma conversa com asserções por turno (evals/scenarios.yaml). O mesmo conjunto
roda com qualquer perfil de LLM e com o **oráculo**: um "modelo" que só devolve os dados que
recebeu. O oráculo mede o encanamento (o dado certo chegou ao modelo?) e roda em segundos, no
CI; a diferença entre ele e um modelo real é o que o modelo erra ao redigir.
"""

from __future__ import annotations

import json
import statistics
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from emporio.agent.agent import Agent, Turn
from emporio.agent.llm import ChatResult

# ------------------------------------------------------------------------------ formato


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intents: list[str] | None = None
    tools: list[str] = []
    contains: list[str] = []
    contains_any: list[str] = []
    not_contains: list[str] = []
    guardrail: str | None = None
    no_llm: bool | None = None
    escalated: bool | None = None
    fallback: bool = False


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str
    expect: Expect = Expect()


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    steps: list[Step]


def load_scenarios(path: Path) -> list[Scenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios = [Scenario.model_validate(s) for s in raw["scenarios"]]
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("ids de cenário repetidos")
    return scenarios


def fold(text: str) -> str:
    """Minúsculas e sem acento (pontuação preservada: "2.199", "10%", "28/02")."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def check(turn: Turn, e: Expect) -> list[str]:
    """Asserções que falharam neste turno (vazio = passou)."""
    fails: list[str] = []
    answer = fold(turn.answer)
    used = {t.name for t in turn.prefetched} | {t.name for t in turn.tools}
    if e.intents is not None:
        got = sorted(i.value for i in turn.route.intents) if turn.route else []
        if got != sorted(e.intents):
            fails.append(f"assunto {got} ≠ {sorted(e.intents)}")
    if missing := [t for t in e.tools if t not in used]:
        fails.append(f"tools não usadas: {missing}")
    fails += [f"faltou '{c}'" for c in e.contains if fold(c) not in answer]
    if e.contains_any and not any(fold(c) in answer for c in e.contains_any):
        fails.append(f"nenhum de {e.contains_any}")
    fails += [f"não podia conter '{c}'" for c in e.not_contains if fold(c) in answer]
    if e.guardrail is not None and turn.guardrail != e.guardrail:
        fails.append(f"guardrail {turn.guardrail} ≠ {e.guardrail}")
    if e.no_llm is not None and (turn.llm_calls == 0) != e.no_llm:
        fails.append(f"chamadas ao LLM: {turn.llm_calls}")
    if e.escalated is not None and turn.escalated != e.escalated:
        fails.append(f"encaminhado: {turn.escalated}")
    if not e.fallback and turn.fallback:
        fails.append(f"resposta de segurança: {turn.fallback}")
    return fails


# ------------------------------------------------------------------------------ execução


@dataclass
class TurnResult:
    user: str
    answer: str
    failures: list[str]
    llm_calls: int
    latency_s: float
    prompt_tokens: int
    tools: list[str]


@dataclass
class ScenarioResult:
    id: str
    title: str
    turns: list[TurnResult] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(not t.failures for t in self.turns)


AgentFactory = Callable[[], Awaitable[Agent]]


async def run_scenario(scenario: Scenario, make_agent: AgentFactory) -> ScenarioResult:
    """Uma conversa nova por cenário (histórico próprio), turno a turno."""
    result = ScenarioResult(scenario.id, scenario.title)
    agent = await make_agent()
    for step in scenario.steps:
        turn = await agent.respond(step.user)
        result.turns.append(
            TurnResult(
                user=step.user,
                answer=turn.answer,
                failures=check(turn, step.expect),
                llm_calls=turn.llm_calls,
                latency_s=round(turn.latency_s, 2),
                prompt_tokens=turn.prompt_tokens,
                tools=[f"{t.name}*" for t in turn.prefetched] + [t.name for t in turn.tools],
            )
        )
    return result


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1]


def summarize(name: str, model: str, results: list[ScenarioResult]) -> dict[str, Any]:
    llm_turns = [t for r in results for t in r.turns if t.llm_calls > 0]
    latencies = [t.latency_s for t in llm_turns]
    checks = sum(len(t.failures) for r in results for t in r.turns)
    return {
        "perfil": name,
        "modelo": model,
        "cenarios": len(results),
        "aprovados": sum(r.passed for r in results),
        "taxa_aprovacao": round(sum(r.passed for r in results) / max(1, len(results)), 3),
        "falhas_de_assercao": checks,
        "turnos": sum(len(r.turns) for r in results),
        "turnos_com_llm": len(llm_turns),
        "latencia_llm_p50_s": round(_pct(latencies, 50), 1),
        "latencia_llm_p95_s": round(_pct(latencies, 95), 1),
        "prompt_tokens_medio": round(
            statistics.mean([t.prompt_tokens for t in llm_turns]) if llm_turns else 0
        ),
        "cenarios_detalhe": [
            {
                "id": r.id,
                "titulo": r.title,
                "passou": r.passed,
                "erro": r.error,
                "turnos": [t.__dict__ for t in r.turns],
            }
            for r in results
        ],
    }


# ------------------------------------------------------------------------------ oráculo


class OracleLLM:
    """Devolve, como resposta, os dados que recebeu das tools (ou a orientação, se não houver).

    Não redige nada: se um cenário falha com o oráculo, o problema está antes do modelo
    (assunto, pré-busca, dados). Nunca chama tools.
    """

    profile_name = "oraculo"

    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None):
        del tools
        texts = [_flatten(m["content"]) for m in messages if m["role"] == "tool"]
        if not texts:  # sem dados (repetir as orientações contaria como recitação)
            texts.append("Posso ajudar com algo da loja?")
        return ChatResult(" ".join(texts), [], "stop")


def _flatten(content: str) -> str:
    """JSON de tool → texto corrido (os valores, sem as chaves)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return content
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        else:
            out.append(str(v))

    walk(data)
    return " ".join(out)
