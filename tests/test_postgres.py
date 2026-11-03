"""Perfil de escala: Postgres + pgvector passam nas mesmas suítes de contrato do SQLite.

Rodam quando `EMPORIO_TEST_DATABASE_URL` aponta um Postgres com a extensão pgvector (o CI sobe um
serviço `pgvector/pgvector` fixado por digest; localmente, ver README, seção Escala). Cada sessão de
testes usa um schema próprio, criado e apagado aqui: o banco apontado não é alterado fora dele.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from emporio.adapters.embedders import HashingEmbedder
from emporio.adapters.retrievers import HybridRetriever, LexicalRetriever
from emporio.adapters.vector_sqlite import SqliteVectorIndex
from emporio.domain.policies import PolicyStore
from emporio.retrieval.documents import build_documents
from tests.conftest import make_settings
from tests.contract.repository_contract import RepositoryContract
from tests.contract.retriever_contract import RetrieverContract

DSN = os.environ.get("EMPORIO_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not DSN, reason="EMPORIO_TEST_DATABASE_URL não definido (Postgres+pgvector)"
    ),
]


@pytest.fixture(scope="session")
def pg_dsn(built_store: Path):
    """Schema temporário com os dados do pipeline (store.db + vectors.db com vetores fake)."""
    psycopg = pytest.importorskip("psycopg", reason="extra 'scale' não instalado")
    from emporio.adapters.postgres_repo import load_from_pipeline

    schema = f"emporio_test_{secrets.token_hex(4)}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(f'CREATE SCHEMA "{schema}"')
    base = DSN or ""
    dsn = f"{base}{'&' if '?' in base else '?'}options=-csearch_path%3D{schema},public"
    counts = load_from_pipeline(dsn, built_store, built_store.parent / "vectors.db")
    assert counts["products"] == 65
    assert counts["vectors"] == 75
    yield dsn
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture(scope="session")
def pg_repo(pg_dsn):
    from emporio.adapters.postgres_repo import PostgresRepository

    repo = PostgresRepository(pg_dsn)
    yield repo
    repo.close()


class TestPostgresRepository(RepositoryContract):
    @pytest.fixture
    def repo(self, pg_repo):
        return pg_repo


@pytest.fixture(scope="session")
def pg_index(pg_dsn):
    from emporio.adapters.pgvector import PgVectorIndex

    idx = PgVectorIndex(pg_dsn)
    yield idx
    idx.close()


class TestHybridRetrieverOverPgVector(RetrieverContract):
    @pytest.fixture
    def retriever(self, pg_repo, pg_index, built_store):
        policies = built_store.parent / "policies.json"
        lexical = LexicalRetriever(build_documents(pg_repo, policies),
                                   PolicyStore.from_json(policies))  # fmt: skip
        return HybridRetriever(lexical, pg_index, HashingEmbedder())


# ============================================================================ equivalência


def test_documents_are_identical_from_sqlite_and_postgres(pg_repo, built_store):
    """Mesmos documentos (e hashes) dos dois backends: o cache de vetores continua válido."""
    policies = built_store.parent / "policies.json"
    assert build_documents(pg_repo, policies) == build_documents(built_store, policies)


@pytest.mark.parametrize(
    "query", ["violão para iniciante", "posso devolver?", "ukulele soprano", "pagamento no pix"]
)
def test_pgvector_ranks_like_the_sqlite_index(pg_index, built_store, query):
    sqlite_idx = SqliteVectorIndex(built_store.parent / "vectors.db")
    qv = HashingEmbedder().embed_query(query)
    pg = pg_index.search(qv, k=5)
    lite = sqlite_idx.search(qv, k=5)
    assert [d for d, _ in pg] == [d for d, _ in lite]
    assert [round(s, 4) for _, s in pg] == [round(s, 4) for _, s in lite]
    assert pg_index.model_id == sqlite_idx.model_id


def test_kind_filter_in_sql(pg_index):
    qv = HashingEmbedder().embed_query("violão")
    assert {pg_index.kind_of(d) for d, _ in pg_index.search(qv, k=10, kind="policy")} == {"policy"}


# ============================================================================ garantias


def test_connection_is_read_only(pg_repo):
    import psycopg

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        pg_repo._con.execute("DELETE FROM products")


def test_sql_injection_is_inert(pg_repo):
    import psycopg

    # parâmetro tipado: o texto é rejeitado como inteiro inválido, nunca executado como SQL
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        pg_repo.get_order("1; DROP TABLE orders")
    assert pg_repo.list_products(availability=["disponivel' OR '1'='1"]) == []
    assert pg_repo.get_order(8) is not None  # a tabela continua lá


def test_schema_enforces_the_same_rules(pg_dsn):
    """As decisões de tratamento também são CHECKs no Postgres."""
    import psycopg

    with psycopg.connect(pg_dsn) as con:
        with pytest.raises(psycopg.errors.CheckViolation):
            con.execute("UPDATE orders SET tracking_code = 'XX123' WHERE order_id = 8")
        con.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            con.execute("UPDATE products SET stock_quantity = 0 WHERE product_id = 95")


def test_load_is_idempotent(pg_dsn, built_store):
    from emporio.adapters.postgres_repo import load_from_pipeline

    again = load_from_pipeline(pg_dsn, built_store, built_store.parent / "vectors.db")
    assert again["orders"] == 20


def test_server_runs_on_postgres_backends(pg_dsn, built_store):
    """O servidor MCP inteiro, por configuração: STORE_BACKEND=postgres, VECTOR_BACKEND=pgvector."""
    import anyio

    from emporio.agent.mcp_client import McpToolbox
    from emporio.mcp_server.server import build_server

    settings = make_settings(data_processed_dir=built_store.parent, store_backend="postgres",
                             vector_backend="pgvector", database_url=pg_dsn)  # fmt: skip

    async def go():
        async with McpToolbox(build_server(settings)) as tb:
            produto = await tb.call("detalhar_produto", {"produto": "Takamine GD20"})
            pedido = await tb.call(
                "consultar_pedido", {"numero_pedido": "8", "email_ou_telefone": "(67) 99654-7821"}
            )
            return produto, pedido

    produto, pedido = anyio.run(go)
    assert produto.data is not None
    assert pedido.data is not None
    assert produto.data["produto"]["preco_final"] == 2199
    assert pedido.data["verificado"] is True


def test_postgres_backend_requires_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        make_settings(store_backend="postgres")
