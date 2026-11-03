"""Pedidos (LGPD + troca/devolução), pagamento, frete e horário da loja."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from emporio.adapters.sqlite_repo import SqliteRepository
from emporio.domain.calculators import (
    check_installments,
    installment_min,
    needs_special_freight,
    payment_plan,
    shipping_quote,
)
from emporio.domain.orders import (
    NOT_VERIFIED,
    OrderService,
    classify_reason,
    contact_matches,
    parse_order_id,
)
from emporio.domain.store_hours import TZ, easter, holiday, store_status

REF = date(2026, 3, 25)  # REFERENCE_DATE padrão


@pytest.fixture(scope="module")
def repo(built_store):
    return SqliteRepository(built_store)


@pytest.fixture(scope="module")
def svc(repo):
    return OrderService(repo, REF)


# ============================================================================ identidade (LGPD)


@pytest.mark.parametrize(
    "contact",
    [
        "anacarol.ferreira@coldmail.com",
        "AnaCarol.Ferreira@ColdMail.com ",
        "(67) 99654-7821",
        "67996547821",
        "+55 67 99654-7821",
        "99654-7821",
    ],
)
def test_lookup_accepts_email_or_phone_of_the_owner(svc, contact):
    r = svc.lookup(8, contact)
    assert r.verified
    assert r.view.order.order_id == 8
    assert r.view.first_name == "Ana"


@pytest.mark.parametrize(
    ("order", "contact"),
    [
        (8, "pedro.oliveira@jmail.com"),  # cliente existe, mas o pedido é de outra pessoa
        (8, "7821"),  # poucos dígitos
        (8, ""),
        (999, "anacarol.ferreira@coldmail.com"),  # pedido inexistente
        ("abc", "anacarol.ferreira@coldmail.com"),
    ],
)
def test_failed_verification_is_neutral(svc, order, contact):
    """A mensagem é a mesma em todos os casos: não revela se o pedido existe nem de quem é."""
    r = svc.lookup(order, contact)
    assert not r.verified
    assert r.view is None
    assert r.message == NOT_VERIFIED


def test_duplicated_email_only_opens_own_order(svc):
    """bruno.martins@jmail.com pertence aos clientes 11 e 43: só abre o pedido do titular."""
    assert svc.lookup(10, "bruno.martins@jmail.com").verified  # pedido 10 é do cliente 11
    assert not svc.lookup(1, "bruno.martins@jmail.com").verified


@pytest.mark.parametrize(
    ("raw", "expected"), [("12", 12), ("#12", 12), ("pedido nº 12", 12), (12, 12), ("x", None)]
)
def test_parse_order_id(raw, expected):
    assert parse_order_id(raw) == expected


def test_contact_matches_unit():
    assert contact_matches("A@B.COM", "a@b.com", "67999990000")
    assert not contact_matches("x@b.com", "a@b.com", "67999990000")
    assert contact_matches("5567999990000", "a@b.com", "67999990000")


# ============================================================================ consulta


def test_view_shipped_and_delayed(svc):
    v = svc.lookup(8, "anacarol.ferreira@coldmail.com").view
    assert v.status_label == "enviado"
    assert v.delayed  # previsão 28/02/2026 < 25/03/2026
    text = v.describe()
    assert "BRJL5544332BR" in text
    assert "já passou" in text
    assert "Kala KA-C Concert Mogno" in text


def test_view_installments_and_items(svc):
    v = svc.lookup(15, "aline.monteiro@jmail.com").view
    assert v.payment_label == "cartão de crédito em 12x"
    assert "R$ 8.348,00" in v.describe()
    assert len(v.order.items) == 2


def test_view_cancelled_shows_reason(svc):
    v = svc.lookup(18, "gustavo.nunes@inlook.com").view
    assert v.status_label == "cancelado"
    assert "fora de estoque" in v.describe()


def test_view_charged_total_not_recomputed(svc):
    assert "R$ 3.450,00" in svc.lookup(3, "isabela.moreira@jmail.com").view.describe()


# ============================================================================ troca e devolução


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("me arrependi da compra", "arrependimento"),
        ("não quero mais", "arrependimento"),
        ("o violão veio quebrado", "defeito"),
        ("parou de funcionar", "defeito"),
        ("queria trocar por outra cor", "preferencia"),
        ("oi", None),
    ],
)
def test_classify_reason(text, reason):
    assert classify_reason(text) == reason


def test_regret_out_of_window(svc):
    a = svc.assess_return(7, "leticia.rocha@jmail.com", "me arrependi")
    assert a.verified
    assert a.reason == "arrependimento"
    assert not a.eligible
    assert a.deadline == date(2026, 2, 24)  # entrega 17/02 + 7 dias
    assert "Fora do prazo" in a.message
    assert "não é mais possível" in a.message  # o Bonsai lia "0 dias restantes" como "ainda dá"
    assert a.days_left is None
    assert any("defeito" in s for s in a.next_steps)
    assert not any("pergunte" in s.lower() for s in a.next_steps)  # texto para o cliente


def test_regret_inside_window_with_other_reference_date(repo):
    svc = OrderService(repo, date(2026, 2, 20))  # 3 dias após a entrega do pedido 7
    a = svc.assess_return(7, "leticia.rocha@jmail.com", "arrependimento")
    assert a.eligible
    assert a.days_left == 4
    assert any("frete de devolução por conta da loja" in c.lower() for c in a.conditions)
    assert any("10 dias úteis" in c for c in a.conditions)


def test_defect_after_30_days_goes_to_legal_warranty(svc):
    a = svc.assess_return(7, "(67) 99789-0123", "veio com defeito")
    assert a.reason == "defeito"
    assert not a.eligible
    assert a.deadline == date(2026, 3, 5)  # compra 03/02 + 30
    assert any("Garantia legal de 90 dias válida até 18/05/2026" in s for s in a.next_steps)
    assert any("intermediar" in s for s in a.next_steps)


def test_defect_inside_30_days(repo):
    svc = OrderService(repo, date(2026, 2, 20))
    a = svc.assess_return(7, "leticia.rocha@jmail.com", "defeito")
    assert a.eligible
    assert a.days_left == 13


def test_defect_after_legal_warranty_mentions_only_manufacturer(svc):
    a = svc.assess_return(1, "pedro.oliveira@jmail.com", "defeito")  # entregue há 151 dias
    assert not any("Garantia legal" in s for s in a.next_steps)
    assert any("fabricante" in s for s in a.next_steps)


def test_preference_window(repo):
    a = OrderService(repo, date(2026, 2, 22)).assess_return(
        7, "leticia.rocha@jmail.com", "trocar por outra cor"
    )
    assert a.reason == "preferencia"
    assert a.eligible
    assert any("disponibilidade" in c for c in a.conditions)


@pytest.mark.parametrize(
    ("order", "contact", "word"),
    [
        (8, "anacarol.ferreira@coldmail.com", "ainda não foi entregue"),
        (13, "fernanda.ribeiro@coldmail.com", "ainda não foi entregue"),
        (19, "pedro.oliveira@jmail.com", "cancelado"),
    ],
)
def test_return_on_not_delivered_orders(svc, order, contact, word):
    a = svc.assess_return(order, contact, "me arrependi")
    assert not a.eligible
    assert word in a.message


def test_return_asks_reason_when_unclear(svc):
    a = svc.assess_return(7, "leticia.rocha@jmail.com", "")
    assert a.reason is None
    assert "me conta o motivo" in a.message


def test_return_requires_verification(svc):
    a = svc.assess_return(7, "errado@x.com", "defeito")
    assert not a.verified
    assert a.message == NOT_VERIFIED


# ============================================================================ pagamento (§3)


@pytest.mark.parametrize(
    ("n", "minimum"), [(2, 50), (3, 50), (4, 80), (6, 80), (7, 100), (12, 100)]
)
def test_installment_tiers(n, minimum):
    assert installment_min(n) == minimum


@pytest.mark.parametrize(
    ("amount", "max_n"),
    [(2199, 12), (599.9, 6), (349.9, 4), (159.9, 3), (99.0, 1), (1200, 12), (790, 7)],
)
def test_max_installments(amount, max_n):
    assert payment_plan(amount).max_installments == max_n


def test_payment_plan_pix_and_combination():
    plan = payment_plan(2199)
    assert plan.pix == pytest.approx(2089.05)
    assert plan.combination_allowed
    assert not payment_plan(2000).combination_allowed  # "acima de" R$ 2.000
    assert "12x sem juros de R$ 183,25" in plan.describe()


def test_payment_plan_with_promotion_has_no_pix_discount():
    plan = payment_plan(5519.08, has_promotion=True)
    assert plan.pix is None
    assert "já em promoção" in plan.describe()


def test_check_installments():
    ok, msg = check_installments(2199, 10)
    assert ok
    assert "10x" in msg
    ok, msg = check_installments(599.9, 10)
    assert not ok
    assert "máximo é 6x" in msg
    assert check_installments(1000, 13)[0] is False


def test_payment_plan_rejects_non_positive():
    with pytest.raises(ValueError, match="positivo"):
        payment_plan(0)


# ============================================================================ frete (§5)


def test_metro_shipping_fee_and_free():
    assert shipping_quote(349.9, "Campo Grande").options[0].price == 35.0
    assert shipping_quote(500, "campo grande").options[0].price == 0.0  # exatos R$ 500 → grátis
    assert "grátis" in shipping_quote(599, "Campo Grande").describe()


def test_other_cities_use_carriers_and_cep():
    q = shipping_quote(7499, "Dourados")
    assert not q.metro
    assert [o.carrier.split()[0] for o in q.options] == ["SEDEX", "Jadlog", "PAC"]
    assert all(o.price is None for o in q.options)
    assert "CEP" in q.describe()


def test_special_freight_for_large_instruments():
    assert shipping_quote(13285, "Corumbá", category_id=3).special_freight  # bateria
    assert needs_special_freight(4, "Piano Digital X")
    assert not needs_special_freight(2, "Music Man Bass 1X Electric Bass")  # baixo elétrico
    assert "frete especial" in shipping_quote(13285, "Corumbá", category_id=3).describe()


# ============================================================================ horário (§2)


def test_easter_and_good_friday():
    assert easter(2026) == date(2026, 4, 5)
    assert holiday(date(2026, 4, 3)) == "Sexta-feira Santa"
    assert holiday(date(2026, 11, 2)) == "Finados"
    assert holiday(date(2026, 8, 26)) == "Aniversário de Campo Grande"
    assert holiday(date(2026, 11, 3)) is None


@pytest.mark.parametrize(
    ("when", "is_open", "fragment"),
    [
        (datetime(2026, 11, 3, 10, 0, tzinfo=TZ), True, "fecha às 18:00"),  # terça
        (
            datetime(2026, 11, 3, 18, 0, tzinfo=TZ),
            False,
            "amanhã (quarta-feira) às 09:00",
        ),  # 18:00 já fechou
        (datetime(2026, 11, 3, 8, 30, tzinfo=TZ), False, "hoje às 09:00"),
        (datetime(2026, 11, 7, 12, 59, tzinfo=TZ), True, "fecha às 13:00"),  # sábado
        (datetime(2026, 11, 7, 14, 0, tzinfo=TZ), False, "segunda-feira, 09/11"),
        (datetime(2026, 11, 8, 10, 0, tzinfo=TZ), False, "domingo"),
        (datetime(2026, 11, 2, 10, 0, tzinfo=TZ), False, "Finados"),  # feriado numa segunda
        (datetime(2026, 4, 3, 10, 0, tzinfo=TZ), False, "Sexta-feira Santa"),
    ],
)
def test_store_status(when, is_open, fragment):
    st = store_status(when)
    assert st.is_open is is_open
    assert fragment in st.describe()


def test_store_status_converts_timezone():
    utc_14h = datetime(2026, 11, 3, 14, 0, tzinfo=TZ).astimezone(
        __import__("zoneinfo").ZoneInfo("UTC")
    )
    assert store_status(utc_14h).is_open  # 14:00 em Campo Grande = 18:00 UTC


# ============================================================================ CLI


def test_cli_order_and_rules(built_store, monkeypatch, capsys):
    from emporio.cli import main

    monkeypatch.setenv("DATA_PROCESSED_DIR", str(built_store.parent))
    assert main(["order", "show", "8", "--contato", "(67) 99654-7821"]) == 0
    assert "BRJL5544332BR" in capsys.readouterr().out
    assert main(["order", "show", "8", "--contato", "outro@x.com"]) == 1
    assert capsys.readouterr().out.strip() == NOT_VERIFIED
    assert main(["order", "return", "7", "--contato", "leticia.rocha@jmail.com",
                 "--motivo", "veio com defeito"]) == 0  # fmt: skip
    assert "18/05/2026" in capsys.readouterr().out
    assert main(["rules", "pay", "599.90", "--parcelas", "10"]) == 0
    assert "máximo é 6x" in capsys.readouterr().out
    assert main(["rules", "ship", "349.90", "--cidade", "Campo Grande"]) == 0
    assert "R$ 35,00" in capsys.readouterr().out
    assert main(["rules", "hours", "--at", "2026-11-02T10:00"]) == 0
    assert "Finados" in capsys.readouterr().out
