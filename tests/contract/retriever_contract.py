"""Suíte de contrato do `Retriever`: todo adaptador (lexical, híbrido, pgvector…) passa nela.

Uso: `class TestMeuRetriever(RetrieverContract)` com uma fixture `retriever`. As perguntas são
de nomes exatos e palavras-chave, que qualquer implementação correta precisa acertar.
"""

from __future__ import annotations

import pytest

from emporio.ports import Retriever


class RetrieverContract:
    @pytest.fixture
    def retriever(self) -> Retriever:  # pragma: no cover - sobrescrita pelas subclasses
        raise NotImplementedError

    def test_satisfies_protocol(self, retriever):
        assert isinstance(retriever, Retriever)

    def test_exact_model_name(self, retriever):
        hits = retriever.search("Takamine GD20", k=3)
        assert hits[0].doc_id == "p95"
        assert hits[0].kind == "product"
        assert hits[0].title.startswith("Takamine GD20")

    def test_policy_by_keyword(self, retriever):
        hits = retriever.search("posso devolver meu pedido?", k=3, kind="policy")
        assert hits[0].doc_id == "trocas_devolucoes"

    def test_respects_k_and_order(self, retriever):
        hits = retriever.search("violão nylon natural", k=4)
        assert 0 < len(hits) <= 4
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)
        assert len({h.doc_id for h in hits}) == len(hits)

    @pytest.mark.parametrize("kind", ["product", "policy"])
    def test_kind_filter(self, retriever, kind):
        hits = retriever.search("frete do violão", k=10, kind=kind)
        assert hits
        assert {h.kind for h in hits} == {kind}

    def test_hits_explain_their_source(self, retriever):
        hit = retriever.search("Takamine GD20", k=1)[0]
        assert hit.ranks
        assert all(pos >= 1 for pos in hit.ranks.values())

    @pytest.mark.parametrize("query", ["receita de bolo de cenoura", "", "???"])
    def test_out_of_scope_returns_nothing(self, retriever, query):
        assert retriever.search(query) == []
