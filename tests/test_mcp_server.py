"""Servidor MCP: tools, resources, prompt, segurança e transportes (memória, stdio e HTTP)."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import TextContent, TextResourceContents

from emporio.domain.store_hours import TZ
from emporio.mcp_server.server import build_server
from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "buscar_produtos",
    "detalhar_produto",
    "consultar_pedido",
    "verificar_devolucao",
    "simular_pagamento",
    "calcular_frete",
    "status_loja",
    "consultar_politica",
}
FINADOS = datetime(2026, 11, 2, 10, 0, tzinfo=TZ)


@pytest.fixture(scope="module")
def processed(built_store) -> Path:
    return built_store.parent


@pytest.fixture
def server(processed):
    return build_server(make_settings(data_processed_dir=processed), now=lambda: FINADOS)


def text(content) -> str:
    """Texto de um conteúdo MCP (falha se não for texto)."""
    assert isinstance(content, TextContent | TextResourceContents), type(content)
    return content.text


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def call(server, tool: str, **args):
    async with Client(server) as c:
        return await c.call_tool(tool, args)


# ============================================================================ descoberta


async def test_lists_exactly_the_store_tools(server):
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for t in tools.values():
        assert t.annotations is not None
        assert t.annotations.read_only_hint is True  # nenhuma tool altera dados
        assert len(t.description or "") > 40  # descrição em PT-BR é o que o LLM lê
        assert t.output_schema  # saída estruturada
    assert set(tools["consultar_pedido"].input_schema["required"]) == {
        "numero_pedido",
        "email_ou_telefone",
    }


async def test_no_way_to_list_customers(server):
    """LGPD: não existe tool nem resource que liste clientes."""
    async with Client(server) as c:
        names = [t.name for t in (await c.list_tools()).tools]
        uris = [str(r.uri) for r in (await c.list_resources()).resources]
    assert not [n for n in names + uris if "client" in n or "customer" in n]


async def test_server_instructions(server):
    async with Client(server) as c:
        assert "Empório da Música" in (c.instructions or "")


# ============================================================================ tools


async def test_detalhar_produto_challenge_question(server):
    r = await call(server, "detalhar_produto", produto="Quanto custa o Takamine GD20?")
    p = r.structured_content["produto"]
    assert r.structured_content["situacao"] == "found"
    assert p["preco_final"] == 2199
    assert p["preco_pix"] == pytest.approx(2089.05)
    assert p["promocao"] is None


async def test_detalhar_produto_promotion_has_no_pix(server):
    p = (await call(server, "detalhar_produto", produto="taylor 110e")).structured_content[
        "produto"
    ]
    assert p["promocao"] == "Desconto Exclusivo (8%)"
    assert p["preco_pix"] is None


async def test_detalhar_produto_unavailable_brings_alternatives(server):
    r = (await call(server, "detalhar_produto", produto="giannini gf-3d")).structured_content
    assert r["produto"]["disponibilidade"] == "esgotado"
    assert len(r["alternativas"]) == 3
    assert "indisponível" in r["orientacao"]


async def test_buscar_produtos(server):
    r = (
        await call(server, "buscar_produtos", texto="violões até R$1000", limite=50)
    ).structured_content
    assert r["situacao"] == "ok"
    assert r["filtros"]["categoria"] == "Violões"
    assert len(r["produtos"]) == 10  # limite máximo da tool
    assert all(p["preco_final"] <= 1000 for p in r["produtos"])


@pytest.mark.parametrize(("texto", "situacao"), [("vocês vendem cordas?", "accessory"),
                                                 ("tem flauta?", "empty_category")])  # fmt: skip
async def test_buscar_produtos_scopes(server, texto, situacao):
    r = (await call(server, "buscar_produtos", texto=texto)).structured_content
    assert r["situacao"] == situacao
    assert r["orientacao"]


async def test_consultar_pedido_verified(server):
    r = (
        await call(
            server, "consultar_pedido", numero_pedido="8", email_ou_telefone="(67) 99654-7821"
        )
    ).structured_content
    assert r["verificado"]
    assert r["cliente"] == "Ana"
    assert r["atrasado"]
    assert r["rastreio"] == "BRJL5544332BR"


async def test_consultar_pedido_not_verified_reveals_nothing(server):
    r = (
        await call(server, "consultar_pedido", numero_pedido="8", email_ou_telefone="x@y.com")
    ).structured_content
    assert not r["verificado"]
    assert r["cliente"] is None
    assert r["itens"] == []
    assert "Não encontrei" in r["resposta"]


async def test_verificar_devolucao(server):
    r = (
        await call(server, "verificar_devolucao", numero_pedido="#7",
                   email_ou_telefone="leticia.rocha@jmail.com", motivo="veio com defeito")
    ).structured_content  # fmt: skip
    assert r["motivo"] == "defeito"
    assert not r["elegivel"]
    assert any("18/05/2026" in s for s in r["proximos_passos"])


async def test_simular_pagamento(server):
    r = (await call(server, "simular_pagamento", valor=599.9, parcelas=10)).structured_content
    assert r["max_parcelas"] == 6
    assert r["parcelas_solicitadas_ok"] is False
    assert "máximo é 6x" in r["resposta"]


async def test_calcular_frete(server):
    r = (
        await call(server, "calcular_frete", valor_pedido=7499, cidade="Dourados",
                   produto_ou_categoria="bateria acústica")
    ).structured_content  # fmt: skip
    assert not r["regiao_metropolitana"]
    assert r["frete_especial"]
    metro = (
        await call(server, "calcular_frete", valor_pedido=600, cidade="Campo Grande")
    ).structured_content
    assert metro["opcoes"][0]["valor"] == 0.0


async def test_status_loja_uses_clock(server):
    r = (await call(server, "status_loja")).structured_content
    assert not r["aberta"]
    assert r["motivo"] == "Finados"
    assert "Black Friday" in r["orientacao"]


async def test_consultar_politica(server):
    r = (
        await call(server, "consultar_politica", pergunta="posso devolver meu pedido?")
    ).structured_content
    assert r["secoes"][0]["id"] == "trocas_devolucoes"
    assert r["secoes"][0]["tokens_estimados"] > 0
    empty = (
        await call(server, "consultar_politica", pergunta="receita de bolo")
    ).structured_content
    assert empty["secoes"] == []
    assert "Não invente" in empty["orientacao"]


# ============================================================================ erros


async def test_expected_error_is_friendly(server):
    r = await call(server, "simular_pagamento", valor=0)
    assert r.is_error
    assert "valor positivo" in text(r.content[0])


async def test_invalid_arguments_are_rejected(server):
    r = await call(server, "simular_pagamento", valor="muito")
    assert r.is_error


async def test_unexpected_error_hides_internals(server, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("segredo interno: /home/ronildo/data/store.db")

    monkeypatch.setattr("emporio.mcp_server.server.payment_plan", boom)
    r = await call(server, "simular_pagamento", valor=100)
    assert r.is_error
    assert "segredo" not in text(r.content[0])
    assert "store.db" not in text(r.content[0])


async def test_logs_never_contain_contact_data(server):
    handler = _Collect()
    records = handler.records
    logger = logging.getLogger("emporio.mcp_server.server")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        await call(
            server, "consultar_pedido", numero_pedido="8", email_ou_telefone="(67) 99654-7821"
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    assert records
    dump = json.dumps([r.__dict__.get("params") for r in records], ensure_ascii=False)
    assert "99654" not in dump
    assert "***" in dump


# ============================================================================ resources e prompt


async def test_policy_resources(server):
    async with Client(server) as c:
        index = json.loads(text((await c.read_resource("politicas://indice")).contents[0]))
        section = text((await c.read_resource("politicas://pagamento")).contents[0])
        templates = [t.uri_template for t in (await c.list_resource_templates()).resource_templates]
    assert len(index) == 10
    assert all(i["tokens_estimados"] > 0 for i in index)  # custo para o orçamento de contexto
    assert "R$ 80,00" in section
    assert "politicas://{secao}" in templates


async def test_unknown_policy_section_errors(server):
    async with Client(server) as c:
        with pytest.raises(Exception, match="não existe"):
            await c.read_resource("politicas://inexistente")


async def test_categories_resource(server):
    async with Client(server) as c:
        cats = json.loads(text((await c.read_resource("catalogo://categorias")).contents[0]))
    assert len(cats) == 9
    assert {c["id"] for c in cats if c["produtos"] == 0} == {6, 7, 8}


async def test_persona_prompt(server):
    async with Client(server) as c:
        plain = text((await c.get_prompt("persona_atendente", {})).messages[0].content)
        named_prompt = await c.get_prompt("persona_atendente", {"nome_cliente": "Ana"})
        named = text(named_prompt.messages[0].content)
    assert "Mel" in plain
    assert "Nunca informe preço" in plain
    assert "(Ana)" in named


# ============================================================================ transportes


def _env(processed: Path) -> dict[str, str]:
    return {**os.environ, "DATA_PROCESSED_DIR": str(processed), "EMBEDDING_BACKEND": "fake"}


async def test_stdio_transport(processed):
    exe = str(Path(sys.executable).parent / "emporio")
    params = StdioServerParameters(command=exe, args=["mcp"], env=_env(processed))
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        r = await c.call_tool("status_loja", {})
    assert names == EXPECTED_TOOLS
    assert "horario_semanal" in r.structured_content


async def test_http_transport(processed):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    exe = str(Path(sys.executable).parent / "emporio")
    proc = subprocess.Popen(
        [exe, "mcp", "--http", "--port", str(port)],
        env=_env(processed), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        async with Client(f"http://127.0.0.1:{port}/mcp") as c:
            names = {t.name for t in (await c.list_tools()).tools}
            r = await c.call_tool("detalhar_produto", {"produto": "95"})
        assert names == EXPECTED_TOOLS
        assert r.structured_content["produto"]["nome"].startswith("Takamine GD20")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
