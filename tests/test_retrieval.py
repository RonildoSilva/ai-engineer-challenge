"""Busca: documentos, índice vetorial, retrievers (contrato + híbrido + degradação), etapa e CLI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from emporio.adapters.embedders import HashingEmbedder
from emporio.adapters.retrievers import HybridRetriever, LexicalRetriever
from emporio.adapters.vector_sqlite import SqliteVectorIndex, read_cache, write_index
from emporio.cli import main
from emporio.domain.models import Document
from emporio.domain.policies import PolicyStore
from emporio.pipeline.stages import build_pipeline
from emporio.ports import Embedder, EmbedderUnavailableError
from emporio.retrieval.documents import build_documents, text_hash
from emporio.retrieval.factory import build_retriever
from emporio.services import ServerUnavailableError, ensure_server, is_healthy, port_in_use
from tests.conftest import make_settings
from tests.contract.retriever_contract import RetrieverContract


@pytest.fixture(scope="module")
def processed(built_store) -> Path:
    return built_store.parent


@pytest.fixture(scope="module")
def docs(processed) -> list[Document]:
    return build_documents(processed / "store.db", processed / "policies.json")


@pytest.fixture(scope="module")
def lexical(docs, processed) -> LexicalRetriever:
    return LexicalRetriever(docs, PolicyStore.from_json(processed / "policies.json"))


# ============================================================================ documentos


def test_documents_cover_catalog_and_manual(docs):
    kinds = [d.kind for d in docs]
    assert kinds.count("product") == 65
    assert kinds.count("policy") == 10
    assert len({d.id for d in docs}) == 75


def test_unreliable_description_not_embedded(docs):
    by_id = {d.id: d for d in docs}
    assert "Jazz" not in by_id["p135"].text  # descrição falava de Fender Jazz Bass
    assert "Ideal para estudantes" in by_id["p81"].text  # descrição confiável entra
    assert "strings: 4" in by_id["p135"].text  # especificações sempre entram


def test_policy_lexical_text_includes_curated_keywords(docs):
    trocas = next(d for d in docs if d.id == "trocas_devolucoes")
    assert "arrependi" in trocas.lexical_text


def test_text_hash_changes_with_content(docs):
    d = docs[0]
    assert text_hash(d) == text_hash(Document(d.id, d.kind, d.title, d.text, "x"))
    assert text_hash(d) != text_hash(Document(d.id, d.kind, d.title, d.text + ".", d.lexical_text))


# ============================================================================ índice vetorial


def test_vector_index_roundtrip_and_kind_filter(tmp_path):
    path = tmp_path / "v.db"
    rows = [
        ("a", "product", "A", "h1", [1.0, 0.0]),
        ("b", "policy", "B", "h2", [0.0, 3.0]),  # não normalizado: o índice normaliza
        ("c", "product", "C", "h3", [0.6, 0.8]),
    ]
    write_index(path, rows, {"model_id": "m1"})
    idx = SqliteVectorIndex(path)
    assert len(idx) == 3
    assert idx.model_id == "m1"
    assert [d for d, _ in idx.search([1.0, 0.0], k=2)] == ["a", "c"]
    assert [d for d, _ in idx.search([1.0, 0.0], k=5, kind="policy")] == ["b"]
    assert idx.search([0.0, 1.0], k=1)[0][1] == pytest.approx(1.0)


def test_cache_only_for_same_model(tmp_path):
    path = tmp_path / "v.db"
    write_index(path, [("a", "product", "A", "h1", [1.0, 0.0])], {"model_id": "m1"})
    assert set(read_cache(path, "m1")) == {"h1"}
    assert read_cache(path, "outro") == {}
    assert read_cache(tmp_path / "nao.db", "m1") == {}


def test_empty_index(tmp_path):
    write_index(tmp_path / "v.db", [], {"model_id": "none"})
    idx = SqliteVectorIndex(tmp_path / "v.db")
    assert len(idx) == 0
    assert idx.search([1.0], k=3) == []


# ============================================================================ contrato


class TestLexicalRetriever(RetrieverContract):
    @pytest.fixture
    def retriever(self, lexical):
        return lexical


class TestHybridRetrieverWithHashing(RetrieverContract):
    @pytest.fixture
    def retriever(self, lexical, processed):
        return HybridRetriever(
            lexical, SqliteVectorIndex(processed / "vectors.db"), HashingEmbedder()
        )


# ============================================================================ híbrido


class StubEmbedder:
    """Embedder controlado: vetor da consulta definido pelo teste; pode simular queda."""

    model_id = "stub"

    def __init__(self, query_vec, *, fail: bool = False):
        self.query_vec = query_vec
        self.fail = fail

    def embed_documents(self, docs):  # pragma: no cover - não usado aqui
        return [[0.0] * len(self.query_vec) for _ in docs]

    def embed_query(self, text):
        if self.fail:
            raise EmbedderUnavailableError("servidor fora do ar")
        return self.query_vec


@pytest.fixture
def tiny(tmp_path, lexical):
    """Índice com 3 produtos reais e vetores escolhidos à mão."""
    path = tmp_path / "v.db"
    rows = [
        ("p95", "product", "Takamine GD20 Dreadnought Natural", "h", [1.0, 0.0, 0.0]),
        ("p132", "product", "Ibanez RG550 Genesis Collection", "h", [0.0, 1.0, 0.0]),
        ("privacidade", "policy", "Privacidade", "h", [0.0, 0.0, 1.0]),
    ]
    write_index(path, rows, {"model_id": "stub"})
    return lambda emb, **kw: HybridRetriever(lexical, SqliteVectorIndex(path), emb, **kw)


def test_stub_satisfies_protocol():
    assert isinstance(StubEmbedder([1.0]), Embedder)


def test_vector_only_hit_is_returned(tiny):
    """Pergunta sem palavras em comum: só a parte vetorial encontra (caso 'esquecer cadastro')."""
    r = tiny(StubEmbedder([0.0, 0.0, 1.0]))
    hits = r.search("como faço pra vocês esquecerem meu cadastro?")
    assert hits[0].doc_id == "privacidade"
    assert hits[0].ranks == {"vector": 1}
    assert r.degraded_reason is None


def test_fusion_rewards_agreement(tiny):
    r = tiny(StubEmbedder([1.0, 0.0, 0.0]))
    hit = r.search("Takamine GD20")[0]
    assert hit.doc_id == "p95"
    assert set(hit.ranks) == {"lexical", "vector"}


def test_low_similarity_vector_results_are_ignored(tiny):
    r = tiny(StubEmbedder([0.2, 0.2, 0.2]), min_similarity=0.9)
    assert r.search("receita de bolo") == []


def test_degrades_to_lexical_when_service_down_and_recovers(tiny, lexical):
    stub = StubEmbedder([1.0, 0.0, 0.0], fail=True)
    r = tiny(stub)
    hits = r.search("Takamine GD20", k=3)
    assert [h.doc_id for h in hits] == [h.doc_id for h in lexical.search("Takamine GD20", k=3)]
    assert "fora do ar" in (r.degraded_reason or "")
    stub.fail = False  # serviço voltou: próxima consulta já é híbrida
    assert "vector" in r.search("Takamine GD20")[0].ranks
    assert r.degraded_reason is None


def test_empty_index_is_permanent_degradation(tmp_path, lexical):
    write_index(tmp_path / "v.db", [], {"model_id": "none"})
    r = HybridRetriever(lexical, SqliteVectorIndex(tmp_path / "v.db"), StubEmbedder([1.0]))
    assert "vazio" in (r.degraded_reason or "")
    assert r.search("Takamine GD20")[0].ranks == {"lexical": 1}


def test_model_mismatch_degrades(tiny):
    other = StubEmbedder([1.0, 0.0, 0.0])
    other.model_id = "outro-modelo"
    r = tiny(other)
    assert "outro modelo" in (r.degraded_reason or "")
    assert r.search("Takamine GD20")[0].ranks == {"lexical": 1}


# ============================================================================ etapa embed_index


def _run(settings):
    return build_pipeline(settings).run(["embed_index"]).by_name()["embed_index"]


def test_stage_reuses_cached_vectors(tmp_path):
    settings = make_settings(data_processed_dir=tmp_path / "p")
    first = _run(settings)
    assert first.metrics["embedded"] == 75
    assert first.metrics["reused"] == 0
    second = build_pipeline(settings).run(["embed_index"], force=True).by_name()["embed_index"]
    assert second.metrics["embedded"] == 0
    assert second.metrics["reused"] == 75


def test_stage_without_embeddings_degrades_but_succeeds(tmp_path):
    settings = make_settings(data_processed_dir=tmp_path / "p", embedding_backend="none")
    result = _run(settings)
    assert result.status == "ok"
    assert result.metrics["vectors"] == 0
    assert isinstance(build_retriever(settings), LexicalRetriever)


def test_stage_with_missing_model_degrades_and_reruns_when_it_appears(tmp_path):
    model = tmp_path / "modelo.gguf"
    settings = make_settings(
        data_processed_dir=tmp_path / "p",
        embedding_backend="llamafile",
        embedding_base_url="http://127.0.0.1:9/v1",  # porta sem serviço
        embedding_model_file=model,
    )
    result = _run(settings)
    assert result.status == "ok"
    assert "modelo ausente" in result.metrics["degraded"]
    model.write_bytes(b"modelo baixado depois")
    assert build_pipeline(settings).status()["embed_index"] == "stale"  # refaz sozinha


def test_index_metadata(processed):
    con = sqlite3.connect(processed / "vectors.db")
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    n = con.execute("SELECT count(*) FROM vectors").fetchone()[0]
    con.close()
    assert meta["model_id"] == HashingEmbedder().model_id
    assert n == 75


# ============================================================================ servidores


def test_is_healthy_false_without_server():
    assert not is_healthy("http://127.0.0.1:9/v1", timeout=0.5)


def test_port_in_use(tmp_path):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert port_in_use("127.0.0.1", s.getsockname()[1])


def test_ensure_server_without_model_fails_clearly(tmp_path):
    with (
        pytest.raises(ServerUnavailableError, match=r"setup\.sh"),
        ensure_server("embedding", "http://127.0.0.1:9/v1", tmp_path / "x.gguf"),
    ):
        pass  # pragma: no cover


# ============================================================================ CLI


def test_cli_search(processed, monkeypatch, capsys):
    monkeypatch.setenv("DATA_PROCESSED_DIR", str(processed))
    assert main(["search", "Takamine GD20", "-k", "2"]) == 0
    out = capsys.readouterr().out
    assert "[HybridRetriever]" in out
    assert "p95" in out.splitlines()[1]
    assert main(["search", "receita de bolo"]) == 0
    assert "nenhum resultado" in capsys.readouterr().out


def test_vectors_are_unit_length(processed):
    idx = SqliteVectorIndex(processed / "vectors.db")
    assert np.allclose(np.linalg.norm(idx.matrix, axis=1), 1.0, atol=1e-5)


def test_policies_json_unchanged_by_retrieval(processed):
    assert len(json.loads((processed / "policies.json").read_text())) == 10
