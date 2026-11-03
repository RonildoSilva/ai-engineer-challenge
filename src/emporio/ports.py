"""Interfaces (ports) estáveis. Os adaptadores concretos ficam em `emporio.adapters`.

Trocar SQLite por Postgres, ou adicionar pgvector, significa escrever um novo adaptador que
satisfaça o mesmo `Protocol` e passe na mesma suíte de testes de contrato (`tests/contract/`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

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


@dataclass(frozen=True)
class SessionInfo:
    id: str
    profile: str
    created_at: str
    updated_at: str
    messages: int


@runtime_checkable
class HistoryStore(Protocol):
    """Histórico de conversas. Implementações: SQLite (padrão) e Postgres (perfil de escala).

    Contrato de privacidade: e-mail e telefone nunca são gravados (mascarados em `append`).
    """

    def create_session(self, profile: str) -> str: ...

    def get_session(self, session_id: str) -> SessionInfo | None: ...

    def sessions(self, limit: int = 20) -> list[SessionInfo]:
        """Mais recentes primeiro."""
        ...

    def delete_session(self, session_id: str) -> None: ...

    def append(
        self, session_id: str, role: str, content: str, meta: dict[str, Any] | None = None
    ) -> None: ...

    def messages(
        self, session_id: str, window: int | None = None, *, skip_blocked: bool = False
    ) -> list[dict[str, Any]]: ...

    def turns_meta(self, session_id: str) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...
