"""Catálogo: preço (promoção × PIX), texto livre → filtros, busca, detalhe e alternativas."""

from __future__ import annotations

import pytest

from emporio.adapters.retrievers import LexicalRetriever
from emporio.adapters.sqlite_repo import SqliteRepository
from emporio.domain.catalog import (
    CatalogService,
    descriptive_terms,
    detect_accessory,
    detect_category,
    extract_price_range,
    format_brl,
    price_info,
)
from emporio.domain.policies import PolicyStore
from emporio.retrieval.documents import build_documents


@pytest.fixture(scope="module")
def repo(built_store):
    return SqliteRepository(built_store)


@pytest.fixture(scope="module")
def svc(repo, built_store):
    processed = built_store.parent
    lexical = LexicalRetriever(
        build_documents(processed / "store.db", processed / "policies.json"),
        PolicyStore.from_json(processed / "policies.json"),
    )
    return CatalogService(repo, lexical)


# ============================================================================ preço


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2199, "R$ 2.199,00"),
        (599.9, "R$ 599,90"),
        (5519.08, "R$ 5.519,08"),
        (12500, "R$ 12.500,00"),
    ],
)
def test_format_brl(value, expected):
    assert format_brl(value) == expected


def test_price_without_promotion_offers_pix(repo):
    """Exemplo do enunciado: o GD20 tem promoção INATIVA → preço cheio + 5% no PIX."""
    info = price_info(repo.get_product(95))
    assert info.final_price == 2199
    assert info.promotion is None
    assert info.pix_price == pytest.approx(2089.05)
    assert "R$ 2.199,00" in info.describe()
    assert "R$ 2.089,05 no PIX" in info.describe()


def test_promotion_shows_original_and_percent_and_no_pix(repo):
    """§6.2: promocional junto do original e do percentual; PIX não acumula com promoção."""
    info = price_info(repo.get_product(94))
    assert info.final_price == pytest.approx(5519.08)
    assert info.pix_price is None
    text = info.describe()
    assert "R$ 5.519,08" in text
    assert "8%" in text
    assert "R$ 5.999,00" in text
    assert "não se soma" in text


# ============================================================================ texto livre


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Quais violões custando até R$1000?", (None, 1000.0)),
        ("até R$ 1.000,00", (None, 1000.0)),
        ("algo abaixo de 500 reais", (None, 500.0)),
        ("entre 500 e 1.200", (500.0, 1200.0)),
        ("entre 1000 e 500", (500.0, 1000.0)),
        ("acima de 2 mil", (2000.0, None)),
        ("a partir de 300", (300.0, None)),
        ("violão Yamaha", (None, None)),
    ],
)
def test_extract_price_range(text, expected):
    assert extract_price_range(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("violões até mil", 5),
        ("ukulele pra criança", 9),
        ("tem baixo de 4 cordas?", 2),
        ("contrabaixo acústico", 8),
        ("vocês têm flauta?", 6),
        ("teclado 61 teclas", 4),
        ("quanto custa o frete?", None),
    ],
)
def test_detect_category(text, expected):
    assert detect_category(text) == expected


@pytest.mark.parametrize(
    ("text", "accessory"),
    [
        ("vocês vendem cordas?", "cordas"),
        ("cordas de violão", "cordas"),
        ("jogo de corda pra guitarra", "corda"),
        ("amplificador para guitarra", "amplificador"),
        ("tem palheta?", "palheta"),
        ("violão de 7 cordas", None),
        ("baixo de quatro cordas", None),
        ("violão com case", None),
        ("guitarra", None),
    ],
)
def test_detect_accessory(text, accessory):
    assert detect_accessory(text) == accessory


def test_descriptive_terms():
    assert descriptive_terms("Quais opções de violões disponíveis custando até R$1000?") == set()
    assert descriptive_terms("ukulele pequeno para criança") == {"pequeno", "crianca"}


# ============================================================================ busca


def test_search_from_challenge_statement(svc):
    """'Quais opções de violões disponíveis custando até R$1000?' (exemplo do enunciado)."""
    r = svc.search("Quais opções de violões disponíveis custando até R$1000?", limit=50)
    assert r.scope == "ok"
    assert r.filters["categoria"] == "Violões"
    assert r.filters["preco_max"] == 1000.0
    assert len(r.items) == 12
    assert all(v.product.category_id == 5 for v in r.items)
    assert all(v.product.is_sellable for v in r.items)
    prices = [v.price.final_price for v in r.items]
    assert max(prices) <= 1000
    assert prices == sorted(prices)  # só filtros → ordenado por preço


def test_search_uses_promotional_price(svc):
    ids = {v.product.product_id for v in svc.search("ukulele até 450").items}
    assert 121 in ids  # R$ 549 com 20% → R$ 439,20


def test_search_only_available_toggle(svc):
    available = {v.product.product_id for v in svc.search("violão até 800", limit=50).items}
    everything = svc.search("violão até 800", only_available=False, limit=50)
    assert 96 not in available  # esgotado
    assert 96 in {v.product.product_id for v in everything.items}


def test_search_explicit_filters_override_text(svc):
    by_name = svc.search(category="Ukuleles", max_price=200, limit=50)
    by_id = svc.search(category=9, max_price=200, limit=50)
    assert {v.product.product_id for v in by_name.items} == {
        v.product.product_id for v in by_id.items
    }
    assert all(v.price.final_price <= 200 for v in by_name.items)


def test_search_ranks_by_description_when_present(svc):
    top = svc.search("violão de 7 cordas", limit=4).items
    assert all("7 Cordas" in v.product.name for v in top)


def test_search_respects_limit(svc):
    assert len(svc.search("violão", limit=3).items) == 3


@pytest.mark.parametrize(
    ("text", "scope"),
    [
        ("vocês vendem cordas?", "accessory"),
        ("amplificador para guitarra", "accessory"),
        ("tem flauta?", "empty_category"),
        ("quero um violino", "empty_category"),
        ("guitarra até 100 reais", "no_match"),
    ],
)
def test_search_scopes(svc, text, scope):
    r = svc.search(text)
    assert r.scope == scope
    assert r.items == []
    assert r.note


# ============================================================================ detalhe


@pytest.mark.parametrize(
    "ref", ["95", "p95", 95, "Takamine GD20", "takamine gd 20", "TAKAMINE GD-20"]
)
def test_detail_finds_gd20(svc, ref):
    d = svc.detail(ref)
    assert d.status == "found"
    assert d.product.product.product_id == 95
    assert d.alternatives == []
    assert d.note is None


@pytest.mark.parametrize(
    ("question", "pid"),
    [
        ("Quanto custa o Takamine GD20?", 95),  # exemplo do enunciado
        ("qual o preço do yamaha c40?", 81),
        ("me fala do taylor 110e", 94),
        ("yamaha c40", 81),
    ],
)
def test_detail_from_natural_question(svc, question, pid):
    """O LLM manda frases inteiras: palavras de pedido não podem diluir o nome do modelo."""
    d = svc.detail(question)
    assert d.status == "found"
    assert d.product.product.product_id == pid


def test_detail_category_question_points_to_search(svc):
    d = svc.detail("quanto custa um violão?")
    assert d.status == "not_found"
    assert "busca de produtos" in d.note


@pytest.mark.parametrize(
    ("ref", "ids"), [("takamine", {95, 100, 105, 115}), ("martin d28", {93, 108})]
)
def test_detail_ambiguous_returns_candidates(svc, ref, ids):
    d = svc.detail(ref)
    assert d.status == "ambiguous"
    got = {c.product.product_id for c in d.candidates}
    assert len(got) >= 2
    assert got <= ids


def test_detail_not_found_and_accessory(svc):
    assert svc.detail("xyz123").status == "not_found"
    assert svc.detail("cordas de violão").status == "accessory"


def test_out_of_stock_gets_similar_alternatives(svc):
    d = svc.detail("Giannini GF-3D")
    assert d.product.product.availability == "esgotado"
    assert "indisponível" in d.note
    assert len(d.alternatives) == 3
    assert all(a.product.is_sellable and a.product.category_id == 5 for a in d.alternatives)
    assert "Dreadnought" in d.alternatives[0].product.name  # mesmo tipo de violão


def test_discontinued_gets_same_type_alternatives(svc):
    d = svc.detail(113)  # Shelby SN-7C 7 Cordas, descontinuado
    assert "catálogo" in d.note
    assert all("7 Cordas" in a.product.name for a in d.alternatives)


@pytest.mark.parametrize(("pid", "word"), [(130, "lançamento"), (136, "catálogo")])
def test_not_for_sale_statuses(svc, pid, word):
    d = svc.detail(pid)
    assert word in d.note
    assert d.alternatives
    assert all(a.product.is_sellable for a in d.alternatives)


def test_categories(svc):
    assert len(svc.categories()) == 9


# ============================================================================ CLI


def test_cli_catalog(built_store, monkeypatch, capsys):
    from emporio.cli import main

    monkeypatch.setenv("DATA_PROCESSED_DIR", str(built_store.parent))
    assert main(["catalog", "search", "violões até R$1000", "-k", "3"]) == 0
    out = capsys.readouterr().out
    assert "[ok]" in out
    assert "PIX" in out
    assert main(["catalog", "show", "Quanto custa o Takamine GD20?"]) == 0
    out = capsys.readouterr().out
    assert "[found]" in out
    assert "R$ 2.199,00" in out
    assert main(["catalog", "show", "giannini gf-3d"]) == 0
    assert out.count("alternativa") == 0
    assert capsys.readouterr().out.count("alternativa:") == 3
    assert main(["catalog", "search", "vocês vendem palheta?"]) == 0
    assert "[accessory]" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("tem o violão Giannini GF-3D?", "Giannini GF-3D Dreadnought Sunburst"),  # era "ambíguo"
        ("e o GNF-3?", "Giannini GNF-3 CEQ Elétrico Nylon Natural"),
        ("quanto custa o GD20", "Takamine GD20 Dreadnought Natural"),
    ],
)
def test_model_code_in_the_question_picks_that_product(svc, query, name):
    d = svc.detail(query)
    assert d.status == "found"
    assert d.product.product.name == name


def test_brand_alone_is_still_ambiguous(svc):
    assert svc.detail("takamine").status == "ambiguous"
