"""Id de requisição nos logs: um por turno do agente e um por chamada de tool no MCP."""

from __future__ import annotations

import json
import logging

import pytest

from emporio.log import JsonFormatter, TextFormatter, current_request_id, request_context
from tests.test_agent import ScriptedLLM, answer, run, server  # noqa: F401


class Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(JsonFormatter().format(record)))


@pytest.fixture
def logs():
    h = Collect()
    root = logging.getLogger("emporio")
    level, root.level = root.level, logging.INFO
    root.addHandler(h)
    yield h.lines
    root.removeHandler(h)
    root.setLevel(level)


def test_formatters_include_the_request_id_only_inside_a_context():
    record = logging.makeLogRecord({"name": "emporio.x", "msg": "oi", "levelname": "INFO"})
    assert "request_id" not in json.loads(JsonFormatter().format(record))
    with request_context("abc123"):
        assert json.loads(JsonFormatter().format(record))["request_id"] == "abc123"
        assert "[abc123]" in TextFormatter().format(record)
    assert current_request_id() is None


def test_nested_context_restores_the_outer_id():
    with request_context("turno"):
        with request_context(prefix="mcp-") as inner:
            assert inner.startswith("mcp-")
        assert current_request_id() == "turno"


async def test_each_turn_has_its_own_id_in_every_log(server, logs):  # noqa: F811
    llm = ScriptedLLM([answer("O GD20 custa R$ 2.199,00."), answer("De nada!")])
    turns, _ = await run(server, llm, "Quanto custa o Takamine GD20?", "obrigado")
    first, second = turns[0].request_id, turns[1].request_id
    assert first
    assert first != second
    turn_logs = [ln for ln in logs if ln["msg"] == "turno concluído"]
    assert [ln["request_id"] for ln in turn_logs] == [first, second]
    # a chamada de tool no servidor MCP também é rastreável no log
    tool_logs = [ln for ln in logs if ln["msg"] == "tool executada"]
    assert tool_logs
    assert all(ln.get("request_id") for ln in tool_logs)
