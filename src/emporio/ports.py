"""Interfaces (ports) estáveis. Os adaptadores concretos ficam em `emporio.adapters`.

Trocar SQLite por Postgres, ou adicionar pgvector, significa escrever um novo adaptador que
satisfaça o mesmo `Protocol` e passe na mesma suíte de testes de contrato (`tests/contract/`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from emporio.domain.models import Category, Customer, Order, Product


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
