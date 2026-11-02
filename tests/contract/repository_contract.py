"""Suíte de contrato do `Repository`: todo adaptador (SQLite, Postgres…) deve passar nela.

Uso: crie `class TestMeuAdaptador(RepositoryContract)` com uma fixture `repo` que devolve o
adaptador carregado com os dados do pipeline.
"""

from __future__ import annotations

from datetime import date

import pytest

from emporio.ports import Repository


class RepositoryContract:
    @pytest.fixture
    def repo(self) -> Repository:  # pragma: no cover - sobrescrita pelas subclasses
        raise NotImplementedError

    # ------------------------------------------------------------------ interface

    def test_satisfies_protocol(self, repo):
        assert isinstance(repo, Repository)

    def test_does_not_expose_customer_listing(self, repo):
        """LGPD: não existe forma de listar clientes pelo repositório."""
        assert not [m for m in dir(repo) if "customers" in m and not m.startswith("_")]

    # ------------------------------------------------------------------ categorias

    def test_categories_with_counts(self, repo):
        cats = {c.category_id: c for c in repo.list_categories()}
        assert len(cats) == 9
        assert cats[5].name == "Violões"
        assert cats[5].product_count == 35
        assert {cid for cid, c in cats.items() if c.product_count == 0} == {6, 7, 8}

    # ------------------------------------------------------------------ produtos

    def test_get_product_full_price_when_promo_inactive(self, repo):
        p = repo.get_product(95)
        assert p.name == "Takamine GD20 Dreadnought Natural"
        assert p.price_brl == 2199
        assert p.promotion is None
        assert p.category_name == "Violões"
        assert p.is_sellable
        assert p.specs
        assert all(isinstance(v, str) for v in p.specs.values())

    def test_get_product_with_active_promotion(self, repo):
        p = repo.get_product(94)
        assert p.promotion is not None
        assert (p.promotion.percent, p.promotion.label) == (8, "Desconto Exclusivo")
        assert p.promotion.price_brl == pytest.approx(5519.08)

    def test_get_missing_product(self, repo):
        assert repo.get_product(999_999) is None

    @pytest.mark.parametrize(
        ("pid", "availability"),
        [(96, "esgotado"), (113, "descontinuado"), (130, "em_breve"), (136, "descontinuado")],
    )
    def test_availability_decisions(self, repo, pid, availability):
        p = repo.get_product(pid)
        assert p.availability == availability
        assert not p.is_sellable

    def test_unreliable_descriptions_are_hidden(self, repo):
        hidden = {p.product_id for p in repo.list_products() if p.description is None}
        assert hidden == {135, 136, 137, 139, 140, 142, 144}
        assert repo.get_product(138).description  # consistente → exposta

    def test_unreliable_description_not_in_search_text(self, repo):
        assert "jazz" not in repo.get_product(135).search_text  # descrição falava de Jazz Bass
        assert "cedro" in repo.get_product(84).search_text  # abreviação "Cedr" expandida

    def test_list_products_filters(self, repo):
        items = repo.list_products(category_id=5, max_price=1000, availability=["disponivel"])
        assert items, "deve haver violões disponíveis até R$ 1000"
        for p in items:
            price = p.promotion.price_brl if p.promotion else p.price_brl
            assert p.category_id == 5
            assert price <= 1000
            assert p.is_sellable
        prices = [p.promotion.price_brl if p.promotion else p.price_brl for p in items]
        assert prices == sorted(prices)

    def test_list_products_uses_effective_price(self, repo):
        # Ohana CK-20 (121): R$ 549 com 20% → R$ 439,20; entra no filtro até R$ 450
        ids = {p.product_id for p in repo.list_products(max_price=450)}
        assert 121 in ids

    def test_list_products_min_price_and_limit(self, repo):
        items = repo.list_products(min_price=10_000, limit=3)
        assert len(items) == 3
        assert all(p.price_brl >= 10_000 for p in items)

    def test_list_products_empty_category(self, repo):
        assert repo.list_products(category_id=7) == []

    # ------------------------------------------------------------------ pedidos e clientes

    def test_get_order_with_items(self, repo):
        o = repo.get_order(15)
        assert o.status == "pending"
        assert o.total_brl == 8348
        assert (o.payment_type, o.installments) == ("credito", 12)
        assert {i.product_id for i in o.items} == {82, 106}
        assert o.tracking_code is None
        assert o.estimated_delivery is None

    def test_get_delivered_order(self, repo):
        o = repo.get_order(1)
        assert o.status == "delivered"
        assert o.tracking_code == "BRAB1234567BR"
        assert o.estimated_delivery == date(2025, 10, 25)
        assert o.order_date == date(2025, 10, 15)

    def test_order_total_is_charged_amount(self, repo):
        assert repo.get_order(3).total_brl == 3450  # ≠ soma dos itens (3498), relatório §4

    def test_cancelled_order_keeps_reason(self, repo):
        assert "cancelado" in repo.get_order(17).notes.lower()

    def test_get_missing_order(self, repo):
        assert repo.get_order(999_999) is None

    def test_get_customer_minimal_personal_data(self, repo):
        c = repo.get_customer(2)
        assert c.first_name == "Ana"
        assert c.phone_digits == "67996547821"
        assert c.email == "anacarol.ferreira@coldmail.com"
        assert c.is_metro
        assert not hasattr(c, "name")  # nome completo não sai do banco
        assert repo.get_customer(47).is_metro is False  # Dourados
