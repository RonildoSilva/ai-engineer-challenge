"""Interfaces (ports) estáveis. Os adaptadores concretos ficam em `emporio.adapters`.

Trocar SQLite por Postgres, ou adicionar pgvector, significa escrever um novo adaptador que
satisfaça o mesmo `Protocol` e passe na mesma suíte de testes de contrato (`tests/contract/`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from emporio.domain.models import Category, Customer, Document, Order, Product, SearchHit


@runtime_checkable
class Repository(Protocol):
    """Acesso somente leitura aos dados da loja.

    Não há método para listar clientes: dados pessoais só são lidos pontualmente, para
    verificar a identidade de quem consulta o próprio pedido (LGPD).
    """

    def list_categories(self) -> list[Category]: ...

    def get_product(self, product_id: int) -> Product | None: ...

    def list_products(
        self,
        *,
        category_id: int | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        availability: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[Product]:
        """Ordenado por preço efetivo (promocional, se houver), crescente."""
        ...

    def get_order(self, order_id: int) -> Order | None: ...

    def get_customer(self, customer_id: int) -> Customer | None: ...


@runtime_checkable
class Embedder(Protocol):
    """Transforma textos em vetores. Implementações: servidor llamafile, fake (testes)."""

    @property
    def model_id(self) -> str:
        """Identifica o modelo: vetores de modelos diferentes não podem ser misturados."""
        ...

    def embed_documents(self, docs: Sequence[Document]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbedderUnavailableError(RuntimeError):
    """O serviço de embeddings não respondeu; quem chama deve degradar para busca lexical."""


@runtime_checkable
class Retriever(Protocol):
    """Busca documentos (produtos e seções de políticas) relevantes para uma pergunta."""

    def search(self, query: str, *, k: int = 5, kind: str | None = None) -> list[SearchHit]:
        """Até `k` resultados, do mais para o menos relevante; `kind` filtra product/policy.

        Lista vazia quando nada é relevante o suficiente: é melhor não responder com contexto
        do que responder com o contexto errado.
        """
        ...
