"""Histórico em SQLite, sessão de chat (retomada, transcrição) e a CLI `emporio chat`."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from emporio import cli
from emporio.agent.agent import Agent
from emporio.agent.chat import ChatSession, Transcript
from emporio.agent.history import SqliteHistoryStore, mask_pii
from emporio.agent.mcp_client import McpToolbox
from emporio.agent.router import Intent
from emporio.mcp_server.server import build_server
from tests.conftest import make_settings
from tests.contract.history_contract import HistoryContract
from tests.test_agent import REF, ScriptedLLM, answer

# ============================================================================ histórico


@pytest.fixture
def store(tmp_path):
    s = SqliteHistoryStore(tmp_path / "history.db")
    yield s
    s.close()


class TestSqliteHistoryStore(HistoryContract):
    """A mesma suíte roda no Postgres (tests/test_postgres.py)."""

    @pytest.fixture
    def store(self, tmp_path):
        s = SqliteHistoryStore(tmp_path / "contract.db")
        yield s
        s.close()


def test_mask_pii_keeps_order_numbers_and_prices():
    text = "pedido 12, ana@coldmail.com, (67) 99654-7821, +55 67 3321-4500, R$ 2.199,00 em 10x"
    assert mask_pii(text) == "pedido 12, [e-mail], [telefone], [telefone], R$ 2.199,00 em 10x"


# ============================================================================ sessão


@pytest.fixture
def server(built_store):
    return build_server(make_settings(data_processed_dir=built_store.parent))


async def chat(server, store, llm, *texts, session_id=None, transcript=None):
    async with McpToolbox(server) as toolbox:
        agent = Agent(llm, toolbox, reference_date=REF)
        session = ChatSession(agent, store, profile="bonsai", session_id=session_id,
                              transcript=transcript)  # fmt: skip
        await session.open()
        turns = [await session.send(t) for t in texts]
        return session, turns


async def test_send_persists_question_answer_and_turn_metadata(server, store):
    llm = ScriptedLLM([answer("Pode sim, em até 7 dias.")])
    session, _ = await chat(server, store, llm, "posso devolver?")
    msgs = store.messages(session.session_id)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    meta = store.turns_meta(session.session_id)[0]
    assert meta["intents"] == ["devolucao"]
    assert meta["prefetched"] == ["consultar_politica"]
    assert meta["llm_calls"] == 1
    assert meta["fallback"] is None


async def test_resumed_session_restores_history_and_subject(server, store):
    first, _ = await chat(server, store, ScriptedLLM([]), "cadê meu pedido?")  # resposta pronta

    llm2 = ScriptedLLM([answer("Seu pedido está atrasado.")])
    resumed, (turn,) = await chat(server, store, llm2, "é o 8, telefone (67) 99654-7821",
                                  session_id=first.session_id)  # fmt: skip
    assert resumed.resumed == 2
    roles = [m["role"] for m in llm2.seen[0]]
    assert roles[:4] == ["system", "user", "assistant", "user"]  # histórico voltou ao modelo
    assert llm2.seen[0][1]["content"] == "cadê meu pedido?"
    assert turn.route is not None
    assert turn.route.intents == (Intent.PEDIDO,)  # assunto da sessão anterior continua
    assert [t.name for t in turn.prefetched] == ["consultar_pedido"]
    assert len(store.messages(first.session_id)) == 4


async def test_unknown_session_is_an_error(server, store):
    with pytest.raises(KeyError):
        await chat(server, store, ScriptedLLM([]), session_id="nao-existe")


async def test_new_session_starts_clean(server, store):
    llm = ScriptedLLM([answer("Oi!"), answer("Olá de novo!")])
    async with McpToolbox(server) as toolbox:
        session = ChatSession(Agent(llm, toolbox, reference_date=REF), store, profile="bonsai")
        await session.open()
        await session.send("oi")
        old = session.session_id
        await session.new()
        await session.send("oi")
    assert session.session_id != old
    assert [m["role"] for m in llm.seen[1]] == ["system", "user"]  # sem a conversa anterior


async def test_transcript_records_each_turn_with_tools_and_masks_pii(server, store, tmp_path):
    path = tmp_path / "conversa.md"
    t = Transcript(path, "bonsai", "bonsai", "abc123")
    llm = ScriptedLLM([answer("O GD20 custa R$ 2.199,00."), answer("Me passa o pedido?")])
    await chat(server, store, llm, "Quanto custa o Takamine GD20?", "meu email é a@b.com",
               transcript=t)  # fmt: skip
    md = path.read_text(encoding="utf-8")
    assert "Modelo: `bonsai`" in md
    assert "**Cliente:** Quanto custa o Takamine GD20?" in md
    assert "**Mel:** O GD20 custa R$ 2.199,00." in md
    assert "`detalhar_produto (pré-busca)`" in md
    assert "a@b.com" not in md
    assert "[e-mail]" in md


# ============================================================================ CLI


@pytest.fixture
def cli_env(monkeypatch, tmp_path, server):
    """`emporio chat` com o agente roteirizado e o histórico num diretório temporário."""
    monkeypatch.setenv("DATA_PROCESSED_DIR", str(tmp_path))
    state = SimpleNamespace(llm=None, inputs=[])

    def fake_run_agent(_settings, _profile, body):
        async def go():
            async with McpToolbox(server) as toolbox:
                agent = Agent(state.llm, toolbox, reference_date=REF)
                fake_llm = SimpleNamespace(profile=SimpleNamespace(resolved_model=lambda: "bonsai"))
                return await body(agent, fake_llm)

        import asyncio

        return asyncio.run(go())

    def fake_input(_prompt=""):
        if not state.inputs:
            raise EOFError
        return state.inputs.pop(0)

    monkeypatch.setattr(cli, "_run_agent", fake_run_agent)
    monkeypatch.setattr("builtins.input", fake_input)
    return state


def test_cli_chat_conversation_resume_and_list(cli_env, tmp_path, capsys):
    cli_env.llm = ScriptedLLM([answer("Oi! Sou a Mel."), answer("Pode sim, em até 7 dias.")])
    cli_env.inputs = ["oi", "", "/ajuda", "posso devolver?", "/sair"]
    assert cli.main(["chat", "--transcript", str(tmp_path / "t.md")]) == 0
    out = capsys.readouterr().out
    assert "Mel> Oi! Sou a Mel." in out
    assert "Mel> Pode sim, em até 7 dias." in out
    assert "/nova" in out  # ajuda
    sid = out.split("--session ")[1].split()[0]
    assert (tmp_path / "t.md").read_text(encoding="utf-8").count("**Cliente:**") == 2

    cli_env.llm = ScriptedLLM([answer("De nada!")])
    cli_env.inputs = ["obrigado"]  # depois, EOF (Ctrl+D) encerra
    assert cli.main(["chat", "--session", sid]) == 0
    out = capsys.readouterr().out
    assert "4 mensagens retomadas" in out

    assert cli.main(["chat", "--list"]) == 0
    assert f"{sid}" in capsys.readouterr().out
    assert len(SqliteHistoryStore(tmp_path / "history.db").messages(sid)) == 6


def test_cli_chat_new_session_command(cli_env, capsys):
    cli_env.llm = ScriptedLLM([answer("Oi!"), answer("Oi de novo!")])
    cli_env.inputs = ["oi", "/nova", "oi"]
    assert cli.main(["chat"]) == 0
    assert "Nova sessão" in capsys.readouterr().out


def test_cli_chat_unknown_session(cli_env, capsys):
    assert cli.main(["chat", "--session", "nao-existe"]) == 2
    assert "não existe" in capsys.readouterr().err
