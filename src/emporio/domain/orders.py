"""Pedidos: consulta com verificação de identidade (LGPD) e regras de troca/devolução (§4, §8).

Verificação: número do pedido + e-mail **ou** telefone do titular *daquele* pedido. Uma falha
devolve sempre a mesma mensagem neutra: não revela se o pedido existe nem de quem é.

Datas: "hoje" é a `REFERENCE_DATE` da configuração (os dados vão até mar/2026). Não há data real
de recebimento; a previsão de entrega (`estimated_delivery`) é usada como aproximação.

Regras de troca/devolução (§4) e garantia (§8):
- arrependimento: até 7 dias corridos após o **recebimento**; frete de volta por conta da loja;
  reembolso na mesma forma de pagamento em até 10 dias úteis (§4.1);
- defeito: troca em até 30 dias corridos após a **compra**; depois, garantia legal de 90 dias
  a partir do recebimento (CDC, §8.1) e garantia do fabricante (6 meses a 2 anos, §8.2);
- preferência (cor, modelo, tamanho): até 7 dias, sujeito à disponibilidade (§4.3). O manual não
  diz a partir de quando: adotamos o recebimento, como no arrependimento.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from emporio.data.rules import digits, normalize
from emporio.domain.catalog import format_brl
from emporio.domain.models import Order
from emporio.ports import Repository

STATUS_LABEL = {
    "pending": "aguardando confirmação do pagamento",
    "confirmed": "pagamento confirmado, em separação para envio",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}
PAYMENT_LABEL = {"pix": "PIX", "debito": "cartão de débito", "boleto": "boleto bancário"}
NOT_VERIFIED = (
    "Não encontrei um pedido com esses dados. Confira o número do pedido e use o e-mail ou o "
    "telefone cadastrados na compra."
)
REGRET_DAYS = 7
DEFECT_DAYS = 30
PREFERENCE_DAYS = 7
LEGAL_WARRANTY_DAYS = 90
MIN_PHONE_DIGITS = 8

Reason = Literal["arrependimento", "defeito", "preferencia"]
REASON_TERMS: dict[Reason, tuple[str, ...]] = {
    "defeito": ("defeito", "defeituoso", "quebrad", "nao funciona", "parou", "estragou",
                "veio com problema", "avaria", "chiado", "trincad"),
    "preferencia": ("outra cor", "cor", "tamanho", "outro modelo", "trocar por", "prefiro"),
    "arrependimento": ("arrependi", "arrependimento", "desisti", "nao quero mais", "devolver",
                       "nao gostei", "desistir", "reembolso", "dinheiro de volta"),
}  # fmt: skip


def parse_order_id(value: str | int) -> int | None:
    """'12', '#12', 'pedido 12', 'nº 12' → 12."""
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def classify_reason(text: str) -> Reason | None:
    """Motivo da troca/devolução a partir do texto do cliente (defeito tem prioridade)."""
    t = normalize(text)
    for reason, terms in REASON_TERMS.items():
        if any(re.search(rf"\b{re.escape(term)}", t) for term in terms):
            return reason
    return None


def contact_matches(contact: str, email: str, phone_digits: str) -> bool:
    """E-mail (sem diferenciar maiúsculas) ou telefone (com ou sem DDD/+55)."""
    c = contact.strip().lower()
    if "@" in c:
        return c == email.lower()
    d = digits(c)
    if d.startswith("55") and len(d) >= 12:  # noqa: PLR2004 — +55 DDD número
        d = d[2:]
    return len(d) >= MIN_PHONE_DIGITS and phone_digits.endswith(d)


# ------------------------------------------------------------------------------ resultados


@dataclass(frozen=True)
class OrderView:
    order: Order
    first_name: str
    status_label: str
    payment_label: str
    delayed: bool  # previsão de entrega vencida e ainda não entregue
    next_step: str

    def describe(self) -> str:
        o = self.order
        items = ", ".join(f"{i.quantity}× {i.product_name}" for i in o.items)
        lines = [
            f"Pedido #{o.order_id} de {o.order_date:%d/%m/%Y}: {self.status_label}.",
            f"Itens: {items}. Total: {format_brl(o.total_brl)} ({self.payment_label}).",
        ]
        if o.tracking_code:
            lines.append(
                f"Rastreio: {o.tracking_code} (Correios ou Jadlog); previsão de entrega "
                f"{o.estimated_delivery:%d/%m/%Y}."
            )
        if o.notes:
            lines.append(f"Observação: {o.notes}.")
        lines.append(self.next_step)
        return " ".join(lines)


@dataclass(frozen=True)
class OrderLookup:
    verified: bool
    view: OrderView | None = None
    message: str | None = None


@dataclass(frozen=True)
class ReturnAssessment:
    verified: bool
    reason: Reason | None = None
    eligible: bool = False
    rule: str = ""
    deadline: date | None = None
    days_left: int | None = None
    conditions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    message: str = ""


# ------------------------------------------------------------------------------ serviço


class OrderService:
    def __init__(self, repo: Repository, today: date):
        self._repo = repo
        self.today = today

    def lookup(self, order_ref: str | int, contact: str) -> OrderLookup:
        order, first_name = self._verified(order_ref, contact)
        if order is None:
            return OrderLookup(verified=False, message=NOT_VERIFIED)
        return OrderLookup(verified=True, view=self._view(order, first_name))

    def assess_return(
        self, order_ref: str | int, contact: str, reason: str | None
    ) -> ReturnAssessment:
        order, _ = self._verified(order_ref, contact)
        if order is None:
            return ReturnAssessment(verified=False, message=NOT_VERIFIED)
        kind = reason if reason in REASON_TERMS else classify_reason(reason or "")
        if kind is None:
            return ReturnAssessment(
                verified=True,
                message="Para avaliar, me conta o motivo: você desistiu da compra, o produto veio "
                "com defeito ou quer trocar por outro modelo, cor ou tamanho? Os prazos são "
                "diferentes em cada caso.",
            )
        if order.status == "cancelled":
            return ReturnAssessment(
                verified=True,
                reason=kind,
                message="O pedido foi cancelado: não há o que devolver ou trocar.",
            )
        if order.status != "delivered":
            label = STATUS_LABEL[order.status]
            return ReturnAssessment(
                verified=True,
                reason=kind,
                message=f"O pedido ainda não foi entregue ({label}). O prazo de devolução por "
                "arrependimento só começa a contar no recebimento. Para cancelar antes da "
                "entrega, a equipe da loja avalia o caso e retorna em até 24 horas úteis.",
            )
        return {
            "arrependimento": self._regret,
            "defeito": self._defect,
            "preferencia": self._preference,
        }[kind](order)

    # ------------------------------------------------------------------ regras (§4, §8)

    def _received(self, order: Order) -> date:
        return order.estimated_delivery or order.order_date

    def _window(self, start: date, days: int) -> tuple[date, int]:
        deadline = start + timedelta(days=days)
        return deadline, (deadline - self.today).days

    def _regret(self, order: Order) -> ReturnAssessment:
        deadline, left = self._window(self._received(order), REGRET_DAYS)
        ok = left >= 0
        conditions = [
            "Produto na embalagem original, sem sinais de uso, com acessórios e manuais.",
            "Frete de devolução por conta da loja.",
            "Reembolso na mesma forma de pagamento em até 10 dias úteis.",
        ]
        after = [
            (
                "Se o produto tiver algum defeito, ainda pode valer a troca por defeito (até 30 "
                "dias após a compra) ou a garantia: é só contar o que aconteceu."
            )
        ]
        return ReturnAssessment(
            verified=True,
            reason="arrependimento",
            eligible=ok,
            rule="Arrependimento: até 7 dias corridos após o recebimento, sem justificativa "
            "(§4.1).",
            deadline=deadline,
            days_left=left if ok else None,  # "0 dias restantes" virava "ainda dá" no modelo
            conditions=conditions if ok else [],
            next_steps=[] if ok else after,
            message=self._deadline_text(deadline, left, "devolução por arrependimento"),
        )

    def _defect(self, order: Order) -> ReturnAssessment:
        deadline, left = self._window(order.order_date, DEFECT_DAYS)
        if left >= 0:
            return ReturnAssessment(
                verified=True,
                reason="defeito",
                eligible=True,
                rule="Troca por defeito de fabricação: até 30 dias corridos após a compra (§4.2).",
                deadline=deadline,
                days_left=left,
                conditions=[
                    "Não cobre mau uso, quedas, umidade excessiva ou modificações não autorizadas."
                ],
                message=self._deadline_text(deadline, left, "troca por defeito"),
            )
        legal_end, legal_left = self._window(self._received(order), LEGAL_WARRANTY_DAYS)
        steps = ["A troca direta na loja (30 dias) já passou: o caminho é a garantia (§4.2, §8)."]
        if legal_left >= 0:
            steps.append(
                f"Garantia legal de 90 dias válida até {legal_end:%d/%m/%Y} "
                f"({legal_left} dias restantes)."
            )
        steps += [
            "Garantia do fabricante: de 6 meses a 2 anos, conforme o certificado do produto.",
            "A Empório da Música pode intermediar o acionamento com o fabricante, se pedido.",
        ]
        return ReturnAssessment(
            verified=True,
            reason="defeito",
            eligible=False,
            rule="Troca por defeito: até 30 dias após a compra; depois, garantia (§4.2, §8).",
            deadline=deadline,
            days_left=None,
            conditions=[
                (
                    "A garantia não cobre desgaste natural, mau uso, quedas ou reparos por "
                    "terceiros não autorizados (§8.3)."
                )
            ],
            next_steps=steps,
            message=f"O prazo de troca direta por defeito terminou em {deadline:%d/%m/%Y}: o "
            "caminho agora é a garantia.",
        )

    def _preference(self, order: Order) -> ReturnAssessment:
        deadline, left = self._window(self._received(order), PREFERENCE_DAYS)
        ok = left >= 0
        conditions = [
            "Produto em perfeito estado e na embalagem original.",
            "Diferença de valor cobrada ou reembolsada.",
            "Consulte a disponibilidade do produto desejado no catálogo.",
        ]
        return ReturnAssessment(
            verified=True,
            reason="preferencia",
            eligible=ok,
            rule="Troca por preferência (cor, modelo, tamanho): até 7 dias, se houver o produto "
            "desejado (§4.3).",
            deadline=deadline,
            days_left=left if ok else None,
            conditions=conditions if ok else [],
            message=self._deadline_text(deadline, left, "troca por preferência"),
        )

    @staticmethod
    def _deadline_text(deadline: date, left: int, what: str) -> str:
        """Texto para o cliente; fora do prazo diz explicitamente que não é mais possível."""
        if left >= 0:
            return (
                f"Dentro do prazo: a {what} vale até {deadline:%d/%m/%Y} ({left} dias restantes)."
            )
        return (
            f"Fora do prazo: a {what} valia até {deadline:%d/%m/%Y} (terminou há {-left} dias), "
            "então não é mais possível por esse motivo."
        )

    # ------------------------------------------------------------------ apoio

    def _verified(self, order_ref: str | int, contact: str) -> tuple[Order | None, str]:
        order_id = parse_order_id(order_ref)
        order = self._repo.get_order(order_id) if order_id else None
        if order is None or not contact:
            return None, ""
        customer = self._repo.get_customer(order.customer_id)
        if customer is None or not contact_matches(contact, customer.email, customer.phone_digits):
            return None, ""
        return order, customer.first_name

    def _view(self, order: Order, first_name: str) -> OrderView:
        payment = PAYMENT_LABEL.get(order.payment_type, order.payment_type)
        if order.payment_type == "credito":
            payment = f"cartão de crédito em {order.installments}x"
        delayed = (
            order.status == "shipped"
            and order.estimated_delivery is not None
            and order.estimated_delivery < self.today
        )
        next_step = {
            "pending": "Assim que o pagamento for confirmado, o pedido segue para separação.",
            "confirmed": "O código de rastreio é enviado por e-mail e WhatsApp quando o pedido "
            "for despachado.",
            "shipped": "Acompanhe pelo código de rastreio no site dos Correios ou da Jadlog.",
            "delivered": "Pedido entregue. Em caso de problema, veja as regras de troca e "
            "devolução.",
            "cancelled": "Pedido cancelado; nada será cobrado ou enviado.",
        }[order.status]
        if delayed:
            next_step = (
                f"A previsão de entrega ({order.estimated_delivery:%d/%m/%Y}) já passou: "
                "vamos registrar o caso e a equipe vai verificar com a transportadora, com "
                "retorno em até 24 horas úteis."
            )
        return OrderView(order, first_name, STATUS_LABEL[order.status], payment, delayed, next_step)
