"""Suíte de contrato do `HistoryStore`: todo adaptador (SQLite, Postgres…) deve passar nela.

Uso: `class TestMeuHistorico(HistoryContract)` com uma fixture `store` (adaptador vazio).
"""

from __future__ import annotations

import threading

import pytest

from emporio.ports import HistoryStore


class HistoryContract:
    @pytest.fixture
    def store(self) -> HistoryStore:  # pragma: no cover - sobrescrita pelas subclasses
        raise NotImplementedError

    def test_satisfies_protocol(self, store):
        assert isinstance(store, HistoryStore)

    def test_session_roundtrip_with_masking(self, store):
        sid = store.create_session("bonsai")
        store.append(sid, "user", "meu e-mail é ana@coldmail.com")
        store.append(sid, "assistant", "Obrigada!", {"intents": ["pedido"], "llm_calls": 1})
        assert store.messages(sid) == [
            {"role": "user", "content": "meu e-mail é [e-mail]"},  # nunca grava o dado pessoal
            {"role": "assistant", "content": "Obrigada!"},
        ]
        assert store.turns_meta(sid) == [{"intents": ["pedido"], "llm_calls": 1}]
        info = store.get_session(sid)
        assert info is not None
        assert (info.profile, info.messages) == ("bonsai", 2)
        assert store.get_session("nao-existe") is None

    def test_window_returns_latest_messages_starting_with_the_customer(self, store):
        sid = store.create_session("bonsai")
        for i in range(5):
            store.append(sid, "user", f"pergunta {i}")
            store.append(sid, "assistant", f"resposta {i}")
        last = store.messages(
            sid, window=3
        )  # 3 últimas = resp 3, perg 4, resp 4 → sem a resp solta
        assert [m["content"] for m in last] == ["pergunta 4", "resposta 4"]
        assert len(store.messages(sid, window=4)) == 4
        assert store.messages(sid, window=0) == []

    def test_resume_skips_turns_blocked_by_guardrails(self, store):
        sid = store.create_session("bonsai")
        store.append(sid, "user", "qual a receita de bolo?")
        store.append(sid, "assistant", "Esse assunto foge…", {"guardrail": "fora_do_escopo"})
        store.append(sid, "user", "tem violão?")
        store.append(sid, "assistant", "Temos sim.", {"guardrail": None})
        assert [m["content"] for m in store.messages(sid, skip_blocked=True)] == [
            "tem violão?", "Temos sim.",
        ]  # fmt: skip
        assert len(store.messages(sid)) == 4  # o histórico salvo continua completo

    def test_sessions_most_recent_first_and_delete_cascades(self, store):
        a, b = store.create_session("bonsai"), store.create_session("bonsai")
        store.append(a, "user", "oi")
        assert next(s.id for s in store.sessions()) == a  # a teve mensagem por último
        store.delete_session(a)
        assert store.get_session(a) is None
        assert store.messages(a) == []
        assert [s.id for s in store.sessions()] == [b]

    def test_one_connection_per_thread(self, store):
        """O agente roda o LLM em outra thread; o SQLite não aceita conexão compartilhada."""
        sid = store.create_session("bonsai")
        errors = []

        def worker():
            try:
                store.append(sid, "user", "de outra thread")
            except Exception as e:  # noqa: BLE001 (o teste registra qualquer falha)
                errors.append(e)
            finally:
                store.close()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert errors == []
        assert store.messages(sid)[0]["content"] == "de outra thread"
