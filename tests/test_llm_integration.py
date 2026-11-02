"""Integração ponta a ponta do baseline: runtime llamafile + GGUF Bonsai-1.7B.

Sobe o servidor real com scripts/start_llamafile.sh numa porta livre e valida a API
compatível com OpenAI (chat + tool calling). Lento (~30–60 s em CPU), por isso marcado
como `integration`:

    uv run pytest -m integration

É pulado automaticamente se os artefatos não foram baixados.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "models/runtime/llamafile-0.10.6-thin"
MODEL = ROOT / "models/bonsai-1.7b/Bonsai-1.7B.gguf"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (RUNTIME.exists() and MODEL.exists()),
        reason="runtime/modelo não baixados (./scripts/download_model.sh)",
    ),
]

TOOL = {
    "type": "function",
    "function": {
        "name": "detalhar_produto",
        "description": "Busca preço, estoque e promoção de um produto pelo nome.",
        "parameters": {
            "type": "object",
            "properties": {"nome": {"type": "string"}},
            "required": ["nome"],
        },
    },
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    proc = subprocess.Popen(
        [str(ROOT / "scripts/start_llamafile.sh")],
        env={**os.environ, "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # permite encerrar o grupo de processos inteiro
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        break
            except OSError:
                pass
            if proc.poll() is not None:
                pytest.fail(f"servidor encerrou com código {proc.returncode}")
            time.sleep(1)
        else:
            pytest.fail("servidor não ficou saudável em 120 s")
        yield base
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)


@pytest.fixture(scope="module")
def client(server):
    return OpenAI(base_url=f"{server}/v1", api_key="none")


def test_server_props(server):
    with urllib.request.urlopen(f"{server}/props", timeout=5) as r:
        props = json.load(r)
    assert props["default_generation_settings"]["n_ctx"] == 8192
    assert props["model_alias"] == "bonsai"


def test_chat_answers_in_portuguese(client):
    resp = client.chat.completions.create(
        model="bonsai",
        messages=[{"role": "user", "content": "Responda só com a palavra: olá"}],
        temperature=0,
        max_tokens=20,
    )
    assert "ol" in (resp.choices[0].message.content or "").lower()


def test_tool_calling_structured(client):
    """Caso que o baseline acerta de forma consistente (ver README): pergunta de preço."""
    resp = client.chat.completions.create(
        model="bonsai",
        messages=[
            {"role": "system", "content": "Atendente da Empório da Música. Use ferramentas."},
            {"role": "user", "content": "Quanto custa o Takamine GD20?"},
        ],
        tools=[TOOL],
        temperature=0,
    )
    calls = [c for c in resp.choices[0].message.tool_calls or [] if c.type == "function"]
    assert calls, "esperava um tool_call estruturado"
    assert calls[0].function.name == "detalhar_produto"
    assert "gd20" in json.loads(calls[0].function.arguments)["nome"].lower().replace(" ", "")


# ------------------------------------------------------------------------------ embeddings

EMBED_MODEL = ROOT / "models/embeddinggemma-300m/embeddinggemma-300M-Q8_0.gguf"


@pytest.fixture(scope="module")
def embedding_server():
    if not EMBED_MODEL.exists():
        pytest.skip("modelo de embeddings não baixado (./scripts/download_model.sh)")
    port = free_port()
    proc = subprocess.Popen(
        [str(ROOT / "scripts/start_llamafile.sh")],
        env={**os.environ, "MODE": "embedding", "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        break
            except OSError:
                pass
            if proc.poll() is not None:
                pytest.fail(f"servidor de embeddings encerrou com código {proc.returncode}")
            time.sleep(1)
        else:
            pytest.fail("servidor de embeddings não ficou saudável em 120 s")
        yield base
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)


def test_embeddings_semantic_match(embedding_server):
    """Pergunta sem palavras em comum com o manual encontra a seção certa (caso do relatório)."""
    client = OpenAI(base_url=f"{embedding_server}/v1", api_key="none")
    texts = [
        "task: search result | query: como faço pra vocês esquecerem meu cadastro?",
        "title: Privacidade | text: O cliente pode solicitar a exclusão de seus dados pessoais.",
        "title: Horário | text: Segunda a sexta-feira das 09:00 às 18:00.",
    ]
    vecs = [d.embedding for d in client.embeddings.create(model="emb", input=texts).data]
    assert len(vecs[0]) == 768

    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        return dot / (sum(x * x for x in a) ** 0.5 * sum(y * y for y in b) ** 0.5)

    assert cos(vecs[0], vecs[1]) > cos(vecs[0], vecs[2])


def test_hybrid_search_with_real_embeddings_on_busy_port(built_store):
    """Busca híbrida com o embeddinggemma real, com a porta configurada ocupada por outro serviço.

    O servidor deve subir numa porta livre, a pergunta sem palavras em comum deve achar a seção
    de privacidade e a pergunta fora de escopo não deve trazer nada.
    """
    if not EMBED_MODEL.exists():
        pytest.skip("modelo de embeddings não baixado (./scripts/download_model.sh)")
    from emporio.adapters.embedders import OpenAICompatibleEmbedder
    from emporio.adapters.retrievers import HybridRetriever, LexicalRetriever
    from emporio.adapters.vector_sqlite import SqliteVectorIndex, write_index
    from emporio.config import Settings
    from emporio.domain.policies import PolicyStore
    from emporio.retrieval.documents import build_documents, text_hash
    from emporio.services import ensure_server

    processed = built_store.parent
    docs = [
        d
        for d in build_documents(processed / "store.db", processed / "policies.json")
        if d.kind == "policy"
    ]
    defaults = Settings(_env_file=None)  # type: ignore[call-arg]
    with socket.socket() as busy:  # "outro serviço" ocupando a porta configurada
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        configured = f"http://127.0.0.1:{busy.getsockname()[1]}/v1"
        with ensure_server(
            "embedding", configured, EMBED_MODEL, log_dir=built_store.parent / "logs"
        ) as url:
            assert url != configured
            embedder = OpenAICompatibleEmbedder(
                url,
                "embeddinggemma",
                query_format=defaults.embedding_query_format,
                doc_format=defaults.embedding_doc_format,
            )
            vectors = embedder.embed_documents(docs)
            index_path = processed / "vectors_real.db"
            write_index(
                index_path,
                [
                    (d.id, d.kind, d.title, text_hash(d), v)
                    for d, v in zip(docs, vectors, strict=True)
                ],
                {"model_id": embedder.model_id},
            )
            retriever = HybridRetriever(
                LexicalRetriever(docs, PolicyStore.from_json(processed / "policies.json")),
                SqliteVectorIndex(index_path),
                embedder,
                min_similarity=defaults.retrieval_min_similarity,
            )
            hits = retriever.search("como faço pra vocês esquecerem meu cadastro?")
            assert hits[0].doc_id == "privacidade"
            assert "vector" in hits[0].ranks
            assert retriever.search("receita de bolo de cenoura") == []
