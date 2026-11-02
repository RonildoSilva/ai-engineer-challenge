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
