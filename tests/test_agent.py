"""Agente: persona, loop de function calling via MCP e proteções (com LLM roteirizado)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from emporio.agent.agent import FALLBACK_ANSWER, Agent, ungrounded_prices
from emporio.agent.budget import ContextBudget
from emporio.agent.llm import ChatResult, LLMUnavailableError, ToolCall, _bare_json_tool_call
from emporio.agent.mcp_client import McpToolbox, compact
from emporio.mcp_server.server import build_server
from tests.conftest import make_settings

REF = date(2026, 3, 25)


@dataclass
class ScriptedLLM:
    """Devolve respostas pré-definidas, em ordem, e registra o que recebeu."""

    script: list[ChatResult | Exception]
    seen: list[list[dict[str, Any]]] = field(default_factory=list)
    tools_seen: list[list[dict[str, Any]] | None] = field(default_factory=list)

    def chat(self, messages, *, tools=None):
        self.seen.append(json.loads(json.dumps(messages)))
        self.tools_seen.append(tools)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def answer(text: str) -> ChatResult:
    return ChatResult(text, [], "stop")


def tool(name: str, raw: str = "{}", error: str | None = None, call_id: str = "c1") -> ChatResult:
    args = {} if error else json.loads(raw)
    return ChatResult("", [ToolCall(call_id, name, args, raw, error)], "tool_calls")


@pytest.fixture
def server(built_store):
    return build_server(make_settings(data_processed_dir=built_store.parent))


async def run(server, llm: ScriptedLLM, *questions: str, max_rounds: int = 5, **kwargs):
    async with McpToolbox(server) as toolbox:
        agent = Agent(llm, toolbox, reference_date=REF, max_rounds=max_rounds, **kwargs)
        return [await agent.respond(q) for q in questions], agent


# ============================================================================ prompt e tools


async def test_system_prompt_has_persona_and_context(server):
    llm = ScriptedLLM([answer("Oi! Sou a Mel.")])
    (turn,), _ = await run(server, llm, "oi")
    system = llm.seen[0][0]
    assert system["role"] == "system"
    assert "Mel" in system["content"]
    assert "25/03/2026" in system["content"]  # data de referência
    assert "loja está" in system["content"]  # status da loja (tool status_loja)
    assert turn.answer == "Oi! Sou a Mel."
    assert turn.tools == []


async def test_without_routing_all_mcp_tools_are_offered_in_openai_format(server):
    llm = ScriptedLLM([answer("ok")])
    await run(server, llm, "oi", routing=False)
    tools = llm.tools_seen[0] or []
    assert {t["function"]["name"] for t in tools} >= {"detalhar_produto", "consultar_pedido"}
    detalhar = next(t for t in tools if t["function"]["name"] == "detalhar_produto")
    assert detalhar["type"] == "function"
    assert detalhar["function"]["parameters"]["required"] == ["produto"]


# ============================================================================ loop


async def test_tool_call_then_answer(server):
    llm = ScriptedLLM([tool("detalhar_produto", '{"produto": "Takamine GD20"}'),
                       answer("O GD20 custa R$ 2.199,00.")])  # fmt: skip
    (turn,), _ = await run(server, llm, "Quanto custa o Takamine GD20?")
    assert turn.answer == "O GD20 custa R$ 2.199,00."
    assert turn.llm_calls == 2
    assert [t.name for t in turn.tools] == ["detalhar_produto"]
    assert turn.tools[0].ok
    # o resultado da tool voltou ao modelo como mensagem `tool`, compacto e estruturado
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "c1"
    data = json.loads(tool_msg["content"])
    assert data["produto"]["preco_final"] == 2199
    assert "candidatos" not in data  # listas vazias removidas (menos tokens)
    assert llm.seen[1][-2]["tool_calls"][0]["function"]["name"] == "detalhar_produto"


async def test_history_keeps_only_user_and_answers(server):
    llm = ScriptedLLM([tool("status_loja"), answer("Fechada hoje."), answer("De nada!")])
    turns, agent = await run(server, llm, "a loja abre hoje?", "obrigado")
    assert [t.answer for t in turns] == ["Fechada hoje.", "De nada!"]
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    assert [m["role"] for m in llm.seen[2]][-1] == "user"  # 2º turno sem as mensagens de tool


async def test_invalid_arguments_are_returned_to_the_model(server):
    llm = ScriptedLLM([tool("detalhar_produto", '{"produto": ', error="JSON truncado"),
                       answer("Pode repetir o modelo?")])  # fmt: skip
    (turn,), _ = await run(server, llm, "quanto custa?")
    assert not turn.tools[0].ok
    assert "Argumentos inválidos" in llm.seen[1][-1]["content"]
    assert turn.answer == "Pode repetir o modelo?"


async def test_unknown_tool_lists_available_ones(server):
    llm = ScriptedLLM([tool("apagar_tudo"), answer("ok")])
    (turn,), _ = await run(server, llm, "x")
    assert "não existe" in llm.seen[1][-1]["content"]
    assert "detalhar_produto" in llm.seen[1][-1]["content"]
    assert not turn.tools[0].ok


@pytest.mark.parametrize("garbled", ["detalhar_produtoArguments", "detalhar_produtos"])
async def test_almost_right_tool_name_is_corrected(server, garbled):
    """O Bonsai, com 8 tools no prompt, escreveu 'detalhar_produtoArguments'."""
    llm = ScriptedLLM([tool(garbled, '{"produto": "95"}'), answer("ok")])
    (turn,), _ = await run(server, llm, "x")
    assert turn.tools[0].name == "detalhar_produto"
    assert turn.tools[0].ok


async def test_tool_error_is_returned_to_the_model(server):
    llm = ScriptedLLM([tool("simular_pagamento", '{"valor": 0}'), answer("Qual o valor?")])
    (turn,), _ = await run(server, llm, "parcela?")
    assert not turn.tools[0].ok
    assert "valor positivo" in llm.seen[1][-1]["content"]


async def test_pii_masked_in_trace(server):
    args = '{"numero_pedido": "8", "email_ou_telefone": "(67) 99654-7821"}'
    llm = ScriptedLLM([tool("consultar_pedido", args), answer("Seu pedido está atrasado.")])
    (turn,), _ = await run(server, llm, "cadê meu pedido?")
    assert turn.tools[0].arguments["email_ou_telefone"] == "***"
    assert turn.tools[0].ok  # mas a tool recebeu o valor real


# ============================================================================ proteções


async def test_round_limit_gives_safe_answer(server):
    llm = ScriptedLLM([tool("status_loja", call_id=f"c{i}") for i in range(3)])
    (turn,), _ = await run(server, llm, "x", max_rounds=3)
    assert turn.answer == FALLBACK_ANSWER
    assert "limite" in (turn.fallback or "")
    assert turn.llm_calls == 3


async def test_llm_unavailable_gives_safe_answer(server):
    llm = ScriptedLLM([LLMUnavailableError("conexão recusada")])
    (turn,), _ = await run(server, llm, "oi")
    assert turn.answer == FALLBACK_ANSWER
    assert "indisponível" in (turn.fallback or "")
    assert "24 horas úteis" in turn.answer


async def test_empty_answer_gives_safe_answer(server):
    (turn,), _ = await run(server, ScriptedLLM([answer("   ")]), "oi")
    assert turn.answer == FALLBACK_ANSWER
    assert "vazia" in (turn.fallback or "")


# ============================================================================ pré-busca


def tool_results(messages: list[dict]) -> dict[str, dict]:
    """Resultados pré-buscados que o modelo recebeu, por nome da tool."""
    calls = {c["id"]: c["function"]["name"]
             for m in messages if m.get("tool_calls") for c in m["tool_calls"]}  # fmt: skip
    return {calls[m["tool_call_id"]]: json.loads(m["content"])
            for m in messages if m["role"] == "tool"}  # fmt: skip


async def test_return_question_is_answered_from_prefetched_policy_in_one_call(server):
    """Critério do commit: "posso devolver?" chega ao modelo já com a regra dos 7 dias."""
    llm = ScriptedLLM([answer("Pode sim! Você tem até 7 dias corridos após o recebimento.")])
    (turn,), _ = await run(server, llm, "posso devolver?")
    assert turn.llm_calls == 1
    assert llm.tools_seen[0] is None  # nenhuma definição de tool no prompt
    assert [t.name for t in turn.prefetched] == ["consultar_politica"]
    secoes = tool_results(llm.seen[0])["consultar_politica"]["secoes"]
    assert secoes[0]["titulo"] == "Trocas e devoluções"
    assert "7 (sete) dias corridos após o recebimento" in secoes[0]["conteudo"]
    system = llm.seen[0][0]["content"]
    assert "número do pedido" in system  # orienta a pedir os dados para avaliar um pedido


async def test_product_price_comes_ready_and_without_promotion_label(server):
    """O Bonsai chamou o preço de tabela de "promocional": agora a promoção vem explícita."""
    llm = ScriptedLLM([answer("O GD20 sai por R$ 2.199,00 (ou R$ 2.089,05 no PIX).")])
    (turn,), _ = await run(server, llm, "Quanto custa o Takamine GD20?")
    produto = tool_results(llm.seen[0])["detalhar_produto"]["produto"]
    assert produto["promocao"].startswith("nenhuma")
    assert "não promoção" in produto["promocao"]
    assert produto["disponibilidade"] == "disponível"
    assert produto["preco"] == "R$ 2.199,00 (ou R$ 2.089,05 no PIX, 5% de desconto)"
    assert "preco_final" not in produto  # números soltos ficam de fora da versão enxuta
    assert turn.fallback is None
    assert turn.prompt_tokens < 1200  # era ~2 mil com as 8 tools


async def test_product_price_feeds_the_payment_simulation(server):
    llm = ScriptedLLM([answer("Dá sim: 10x de R$ 219,90 sem juros.")])
    (turn,), _ = await run(server, llm, "quanto custa o GD20 e dá pra fazer em 10x?")
    assert [t.name for t in turn.prefetched] == ["detalhar_produto", "simular_pagamento"]
    assert turn.prefetched[1].arguments == {"valor": 2199.0, "preco_promocional": False,
                                            "parcelas": 10}  # fmt: skip
    pagamento = tool_results(llm.seen[0])["simular_pagamento"]
    assert {"vezes": 10, "valor": 219.9} in pagamento["parcelas"]
    assert turn.answer.startswith("Dá sim")  # 219,90 está nos dados: passa na checagem


async def test_order_without_data_asks_for_it_then_prefetches_on_followup(server):
    llm = ScriptedLLM([answer("Me passa o número do pedido e o e-mail?"),
                       answer("Seu pedido está atrasado.")])  # fmt: skip
    turns, _ = await run(server, llm, "cadê meu pedido?", "é o 8, telefone (67) 99654-7821")
    assert turns[0].prefetched == []
    assert "número do pedido" in llm.seen[0][0]["content"]
    assert turns[1].route is not None
    assert turns[1].route.inherited
    assert [t.name for t in turns[1].prefetched] == ["consultar_pedido"]
    assert turns[1].prefetched[0].arguments["email_ou_telefone"] == "***"
    assert tool_results(llm.seen[1])["consultar_pedido"]["verificado"] is True


async def test_accessory_is_redirected_without_tools(server):
    llm = ScriptedLLM([answer("Não vendemos palhetas, só instrumentos.")])
    await run(server, llm, "vocês vendem palheta?")
    assert llm.tools_seen[0] is None
    assert tool_results(llm.seen[0])["buscar_produtos"]["situacao"] == "accessory"


async def test_unknown_subject_offers_only_generic_tools(server):
    llm = ScriptedLLM([answer("Só posso ajudar com a loja.")])
    (turn,), _ = await run(server, llm, "qual a receita de bolo de cenoura?")
    assert turn.prefetched == []
    assert set(turn.tools_offered) == {"buscar_produtos", "detalhar_produto",
                                       "consultar_politica"}  # fmt: skip


async def test_large_window_also_offers_the_subject_tools(server):
    llm = ScriptedLLM([answer("Pode sim, em até 7 dias.")])
    big = ContextBudget(window=128_000, reserve_output=1024)
    (turn,), _ = await run(server, llm, "posso devolver?", budget=big)
    assert set(turn.tools_offered) == {"verificar_devolucao", "consultar_politica"}
    assert turn.prefetched[0].arguments["limite"] == 3  # mais seções do manual cabem


# ============================================================================ preço sem fonte


def test_ungrounded_prices():
    evidence = ['{"preco":"R$ 2.199,00 (ou R$ 2.089,05 no PIX)"}', '{"valor":219.9}', "até R$1000"]
    assert ungrounded_prices("Custa R$ 2.199,00 ou R$ 2.089,05 no PIX.", evidence) == []
    assert ungrounded_prices("10x de R$ 219,90", evidence) == []
    assert ungrounded_prices("opções até R$ 1.000", evidence) == []  # valor citado pelo cliente
    assert ungrounded_prices("Custa $120.", evidence) == ["$120"]
    assert ungrounded_prices("sai por 1.899 reais", evidence) == ["1.899 reais"]
    assert ungrounded_prices("Você tem 7 dias para devolver.", evidence) == []


async def test_invented_price_gets_one_correction_then_passes(server):
    llm = ScriptedLLM([answer("Custa R$ 120,00!"), answer("Custa R$ 2.199,00.")])
    (turn,), _ = await run(server, llm, "Quanto custa o Takamine GD20?")
    assert turn.price_retry == ["R$ 120,00"]
    assert "R$ 120,00" in llm.seen[1][-1]["content"]  # o modelo foi avisado do valor sem fonte
    assert turn.answer == "Custa R$ 2.199,00."
    assert turn.fallback is None


async def test_invented_price_twice_gives_safe_answer(server):
    llm = ScriptedLLM([answer("Custa $120."), answer("Custa $130.")])
    (turn,), agent = await run(server, llm, "quanto custa um violão bom?")
    assert turn.answer == FALLBACK_ANSWER
    assert "sem fonte" in (turn.fallback or "")
    assert (
        agent.messages[-1]["content"] == FALLBACK_ANSWER
    )  # o valor inventado não fica no histórico


# ============================================================================ normalização


def test_bare_json_tool_call_is_recovered():
    call = _bare_json_tool_call(
        '{"name": "detalhar_produtoArguments", "arguments": {"produto": "GD20"}}'
    )
    assert call is not None
    assert call.name == "detalhar_produtoArguments"
    assert call.arguments == {"produto": "GD20"}
    assert _bare_json_tool_call("O preço é R$ 2.199,00.") is None
    assert _bare_json_tool_call('{"preco": 2199}') is None  # JSON sem "name" não é tool call


def test_compact():
    assert compact({"a": None, "b": [], "c": {}, "d": "", "e": 0, "f": [{"g": None, "h": 1}]}) == {
        "e": 0,
        "f": [{"h": 1}],
    }


# ============================================================================ stdio


async def test_agent_over_stdio_mcp(built_store):
    import os
    import sys
    from pathlib import Path

    env = {**os.environ, "DATA_PROCESSED_DIR": str(built_store.parent), "EMBEDDING_BACKEND": "fake"}
    toolbox = McpToolbox.stdio(str(Path(sys.executable).parent / "emporio"), ["mcp"], env)
    llm = ScriptedLLM([tool("detalhar_produto", '{"produto": "95"}'), answer("R$ 2.199,00")])
    async with toolbox:
        turn = await Agent(llm, toolbox, reference_date=REF).respond("GD20?")
    assert turn.tools[0].ok
    assert json.loads(llm.seen[1][-1]["content"])["produto"]["id"] == 95
