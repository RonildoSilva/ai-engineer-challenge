"""Orçamento de contexto: o mesmo critério em janelas de 8 mil e de 128 mil tokens."""

from __future__ import annotations

from emporio.agent.budget import ContextBudget, estimate_tokens, fit, message_tokens

SMALL = ContextBudget(window=8192, reserve_output=512)  # Bonsai local
LARGE = ContextBudget(window=128_000, reserve_output=1024)  # API online


def msg(role: str, chars: int, **extra) -> dict:
    return {"role": role, "content": "x" * chars, **extra}


def evidence(*sizes: int) -> list[dict]:
    ids = [f"pre{i}" for i in range(len(sizes))]
    call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "t", "arguments": "{}"}} for i in ids
        ],
    }
    return [call, *(msg("tool", n, tool_call_id=i) for i, n in zip(ids, sizes, strict=True))]


def tool_def(name: str, chars: int) -> dict:
    return {"type": "function", "function": {"name": name, "description": "d" * chars}}


def test_available_tokens_leave_room_for_answer_and_error():
    assert SMALL.available == int(8192 * 0.95) - 512
    assert LARGE.available == int(128_000 * 0.95) - 1024
    assert ContextBudget(window=500, reserve_output=512).available == 0


def test_prefetch_size_grows_with_the_window():
    assert SMALL.policy_sections == 2
    assert LARGE.policy_sections == 3
    assert ContextBudget(window=2048, reserve_output=512).policy_sections == 1
    assert SMALL.product_results == 5
    assert ContextBudget(window=2048, reserve_output=512).product_results == 3


def test_pick_sections_uses_the_cost_published_by_mcp():
    tiny = ContextBudget(window=2048, reserve_output=512)  # evidência: até 573 tokens
    sections = [
        {"id": "trocas", "tokens_estimados": 479},
        {"id": "atendimento", "tokens_estimados": 565},  # não cabe junto
        {"id": "horarios", "tokens_estimados": 80},  # cabe (479 + 80)
    ]
    assert [s["id"] for s in tiny.pick_sections(sections)] == ["trocas", "horarios"]
    assert len(LARGE.pick_sections(sections)) == 3
    # a mais relevante sempre entra, mesmo acima do limite
    assert tiny.pick_sections([{"id": "big", "tokens_estimados": 5000}])[0]["id"] == "big"


def test_everything_fits_in_a_large_window():
    history = [msg("user", 400), msg("assistant", 400)] * 20
    f = fit(LARGE, system=msg("system", 2000), history=history, user=msg("user", 100),
            evidence=evidence(2000, 2000), tools=[tool_def("a", 3000)])  # fmt: skip
    assert f.dropped_history == f.dropped_evidence == 0
    assert f.dropped_tools == []
    assert len(f.messages) == 1 + 40 + 1 + 3


def test_small_window_drops_oldest_history_first_in_pairs():
    history = []
    for i in range(30):
        history += [msg("user", 400) | {"n": i}, msg("assistant", 400) | {"n": i}]
    f = fit(SMALL, system=msg("system", 2000), history=history, user=msg("user", 100),
            evidence=evidence(4000), tools=None)  # fmt: skip
    assert f.dropped_history > 0
    assert f.dropped_history % 2 == 0  # nunca separa pergunta e resposta
    kept = [m for m in f.messages if "n" in m]
    assert kept[-1]["n"] == 29  # a conversa mais recente fica
    assert kept[0]["role"] == "user"
    assert f.tokens <= SMALL.available


def test_order_is_system_history_user_then_prefetched_results():
    hist = [msg("user", 10), msg("assistant", 10)]
    f = fit(SMALL, system=msg("system", 10), history=hist, user=msg("user", 10),
            evidence=evidence(10), tools=None)  # fmt: skip
    assert [m["role"] for m in f.messages] == ["system", "user", "assistant", "user",
                                               "assistant", "tool"]  # fmt: skip


def test_evidence_is_never_cut_in_the_middle():
    """Um resultado que não cabe sai inteiro (e sai também da chamada correspondente)."""
    tiny = ContextBudget(window=4096, reserve_output=512)  # evidência: ~1351 tokens
    f = fit(tiny, system=msg("system", 400), history=[], user=msg("user", 40),
            evidence=evidence(2000, 8000, 1200), tools=None)  # fmt: skip
    tool_msgs = [m for m in f.messages if m["role"] == "tool"]
    assert [len(m["content"]) for m in tool_msgs] == [2000, 1200]
    assert f.dropped_evidence == 1
    call = next(m for m in f.messages if m.get("tool_calls"))
    assert [c["id"] for c in call["tool_calls"]] == ["pre0", "pre2"]


def test_tools_that_do_not_fit_are_dropped_and_reported():
    tiny = ContextBudget(window=2048, reserve_output=512)
    f = fit(tiny, system=msg("system", 4000), history=[], user=msg("user", 40), evidence=[],
            tools=[tool_def("a", 1000), tool_def("b", 4000)])  # fmt: skip
    assert [t["function"]["name"] for t in f.tools or []] == ["a"]
    assert f.dropped_tools == ["b"]


def test_estimates():
    assert estimate_tokens("x" * 400) == 100
    assert estimate_tokens({"a": 1}) == 2
    assert message_tokens({"role": "user", "content": None}) == 4
