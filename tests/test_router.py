"""Roteador: intenções (inclusive múltiplas), dados citados e continuidade entre turnos."""

from __future__ import annotations

import pytest

from emporio.agent.router import (
    Intent,
    Slots,
    detect_intents,
    extract_slots,
    product_query,
    route,
)

I = Intent  # noqa: E741 (abreviação só para as tabelas de casos)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # frases reais de clientes (manual §7 e enunciado)
        ("posso devolver?", (I.DEVOLUCAO,)),
        ("Quanto custa o Takamine GD20?", (I.PRODUTO,)),
        ("Quais violões vocês têm até R$1000?", (I.CATALOGO,)),
        ("tem guitarra?", (I.CATALOGO,)),
        ("vocês vendem palheta?", (I.ACESSORIO,)),
        ("tem corda de violão?", (I.ACESSORIO,)),
        ("qual o status do meu pedido?", (I.PEDIDO,)),
        ("cadê minha encomenda?", (I.PEDIDO,)),
        ("posso pagar no pix?", (I.PAGAMENTO,)),
        ("qual o valor do frete pra Dourados?", (I.FRETE,)),
        ("a loja abre amanhã às 18h?", (I.HORARIO,)),
        ("qual o endereço de vocês?", (I.POLITICA,)),
        ("quero excluir meus dados", (I.POLITICA,)),
        ("quero falar com um atendente", (I.POLITICA,)),
        ("oi, tudo bem?", (I.SAUDACAO,)),
        ("obrigado!", (I.SAUDACAO,)),
    ],
)
def test_single_intent(text, expected):
    assert detect_intents(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("quanto custa o GD20 e dá pra fazer em 10x?", {I.PRODUTO, I.PAGAMENTO}),
        ("violões até R$1000 em 10x", {I.CATALOGO, I.PAGAMENTO}),
        ("o pix tem desconto?", {I.PAGAMENTO, I.POLITICA}),
        ("qual o horário e o endereço da loja?", {I.HORARIO, I.POLITICA}),
        ("meu pedido não chegou, qual o prazo de entrega?", {I.PEDIDO, I.FRETE}),
    ],
)
def test_multiple_intents(text, expected):
    assert set(detect_intents(text)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "quero trocar meu violão, veio com defeito",  # citar o violão não é pedir opções
        "quero devolver o pedido nº 12, me arrependi",  # a devolução já consulta o pedido
    ],
)
def test_return_is_not_catalog_nor_order_status(text):
    assert detect_intents(text) == (I.DEVOLUCAO,)


def test_unknown_subject_is_general_with_generic_tools():
    r = route("qual a receita de bolo de cenoura?")
    assert r.intents == (I.GERAL,)
    assert set(r.tools) == {"buscar_produtos", "detalhar_produto", "consultar_politica"}


def test_tools_are_a_small_subset():
    assert route("posso devolver?").tools == ("verificar_devolucao", "consultar_politica")
    assert route("oi").tools == ()
    assert route("vende palheta?").tools == ()
    multi = route("quanto custa o GD20 e dá pra fazer em 10x?").tools
    assert multi == ("detalhar_produto", "buscar_produtos", "simular_pagamento",
                     "consultar_politica")  # fmt: skip


def test_hour_and_installments_are_not_model_codes():
    assert I.PRODUTO not in detect_intents("vocês abrem às 18h?")
    assert I.PRODUTO not in detect_intents("parcela em 12x?")
    assert I.PRODUTO not in detect_intents("meu email é joao123@gmail.com")


# ============================================================================ dados citados


def test_slots_from_one_message():
    s = extract_slots("meu pedido nº 12 não chegou, email pedro.oliveira@jmail.com")
    assert s.order_id == "12"
    assert s.contact == "pedro.oliveira@jmail.com"
    s = extract_slots("pedido #7, telefone (67) 98432-1098")
    assert (s.order_id, s.contact) == ("7", "(67) 98432-1098")
    s = extract_slots("frete pra Dourados de um violão de R$ 1.500,50 em 10x")
    assert (s.amount, s.installments, s.city) == (1500.5, 10, "Dourados")


def test_contact_words_with_contact_data_are_not_a_store_contact_question():
    """'meu telefone é …' é dado do cliente, não pergunta sobre o telefone da loja."""
    assert I.POLITICA not in detect_intents("meu telefone é (67) 98432-1098")
    assert detect_intents("qual o telefone da loja?") == (I.POLITICA,)


def test_followup_with_only_data_inherits_previous_intent():
    first = route("qual o status do meu pedido?")
    assert not first.slots.has_order_identity
    second = route("é o 12, telefone 67 98432-1098", first, ["qual o status do meu pedido?"])
    assert second.inherited
    assert second.intents == (I.PEDIDO,)
    assert second.slots == Slots(order_id="12", contact="67 98432-1098")


def test_slots_are_completed_from_history():
    hist = ["quero devolver o pedido 12, me arrependi"]
    r = route("pedro.oliveira@jmail.com", route(hist[0]), hist)
    assert r.intents == (I.DEVOLUCAO,)
    assert r.slots.has_order_identity
    assert r.slots.order_id == "12"


def test_bare_number_only_counts_as_order_after_an_order_question():
    assert route("12", route("oi")).slots.order_id is None
    assert route("12", route("cadê meu pedido?")).slots.order_id == "12"


def test_product_query_keeps_only_the_clause_with_the_product():
    assert product_query("quanto custa o GD20 e dá pra fazer em 10x?") == "quanto custa o GD20"
    assert product_query("Tem o Yamaha C40? Entrega em Dourados?") == "Tem o Yamaha C40"
    assert product_query("quanto custa?") == "quanto custa?"
