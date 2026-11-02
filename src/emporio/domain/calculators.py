"""Calculadoras de regras de negócio: pagamento (manual §3) e frete (§5). Funções puras.

Suposições documentadas:
- §3.1 "até 3x sem valor mínimo (exceto abaixo de R$ 50,00)" → até 3x, cada parcela ≥ R$ 50.
- §5.1 "frete grátis acima de R$ 500 / taxa abaixo de R$ 500": exatos R$ 500 → grátis
  (o manual não define; a interpretação favorece o cliente).
- Região metropolitana: só Campo Grande (a base de clientes não tem outros municípios da região).
- Frete especial (§5.2) para baterias acústicas, pianos e contrabaixos acústicos. Os baixos
  elétricos do catálogo não entram (o manual cita "contrabaixos" entre os de grande porte).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from emporio.data.rules import is_metro
from emporio.domain.catalog import PIX_DISCOUNT_PERCENT, format_brl

# (máx. parcelas da faixa, parcela mínima)
INSTALLMENT_TIERS = ((3, 50.0), (6, 80.0), (12, 100.0))
COMBINATION_MIN = 2000.0  # combinar formas de pagamento: compras acima de R$ 2.000
FREE_SHIPPING_MIN = 500.0
METRO_FEE = 35.0


# ------------------------------------------------------------------------------ pagamento


@dataclass(frozen=True)
class Installment:
    n: int
    value: float


@dataclass(frozen=True)
class PaymentPlan:
    amount: float
    has_promotion: bool
    pix: float | None  # None quando o preço já é promocional (descontos não acumulam)
    installments: list[Installment]
    combination_allowed: bool
    notes: list[str] = field(default_factory=list)

    @property
    def max_installments(self) -> int:
        return max((i.n for i in self.installments), default=1)

    def describe(self) -> str:
        parts = []
        if self.pix is not None:
            parts.append(f"PIX: {format_brl(self.pix)} ({PIX_DISCOUNT_PERCENT}% de desconto)")
        else:
            parts.append(f"PIX: {format_brl(self.amount)} (sem desconto adicional: já em promoção)")
        parts.append(f"débito ou boleto: {format_brl(self.amount)} à vista")
        best = self.installments[-1]
        parts.append(f"crédito em até {best.n}x sem juros de {format_brl(best.value)}")
        if self.combination_allowed:
            parts.append("dá para combinar formas de pagamento (ex.: PIX + cartão)")
        return "; ".join(parts) + "."


def installment_min(n: int) -> float:
    for max_n, minimum in INSTALLMENT_TIERS:
        if n <= max_n:
            return minimum
    raise ValueError(f"máximo de 12 parcelas (pedido: {n})")


def payment_plan(amount: float, *, has_promotion: bool = False) -> PaymentPlan:
    """Formas de pagamento válidas para o valor (manual §3 e §3.1)."""
    if amount <= 0:
        raise ValueError("valor deve ser positivo")
    installments = [Installment(1, round(amount, 2))]
    for n in range(2, 13):
        value = round(amount / n, 2)
        if value >= installment_min(n):
            installments.append(Installment(n, value))
    notes = ["Boleto: compensação em até 3 dias úteis."]
    if has_promotion:
        notes.append("O desconto do PIX não se aplica sobre preço promocional (§6.2).")
    return PaymentPlan(
        amount=round(amount, 2),
        has_promotion=has_promotion,
        pix=None if has_promotion else round(amount * (1 - PIX_DISCOUNT_PERCENT / 100), 2),
        installments=installments,
        combination_allowed=amount > COMBINATION_MIN,
        notes=notes,
    )


def check_installments(amount: float, n: int) -> tuple[bool, str]:
    """Responde "dá pra parcelar em Nx?" com o motivo."""
    if not 1 <= n <= 12:  # noqa: PLR2004 — limite do manual (até 12x)
        return False, "O parcelamento vai de 1x a 12x sem juros."
    value = round(amount / n, 2)
    minimum = installment_min(n)
    if n > 1 and value < minimum:
        best = payment_plan(amount).max_installments
        return False, (
            f"Em {n}x a parcela seria {format_brl(value)}, abaixo do mínimo de "
            f"{format_brl(minimum)} para essa faixa. Para {format_brl(amount)}, o máximo é {best}x."
        )
    return True, f"Sim: {n}x sem juros de {format_brl(value)}."


# ------------------------------------------------------------------------------ frete


@dataclass(frozen=True)
class ShippingOption:
    carrier: str
    days: str
    price: float | None  # None = calculado pelo CEP/peso/dimensões no fechamento


@dataclass(frozen=True)
class ShippingQuote:
    metro: bool
    options: list[ShippingOption]
    special_freight: bool
    notes: list[str]

    def describe(self) -> str:
        lines = []
        for o in self.options:
            price = format_brl(o.price) if o.price is not None else "valor pelo CEP"
            if o.price == 0:
                price = "grátis"
            lines.append(f"{o.carrier}: {price}, {o.days}")
        return "; ".join(lines) + ". " + " ".join(self.notes)


SPECIAL_CATEGORIES = {3, 8}  # baterias e percussão; cordas orquestrais (contrabaixo acústico)


def needs_special_freight(category_id: int | None, product_name: str = "") -> bool:
    name = product_name.lower()
    return category_id in SPECIAL_CATEGORIES or "piano" in name or "contrabaixo ac" in name


def shipping_quote(
    order_value: float,
    city: str,
    *,
    category_id: int | None = None,
    product_name: str = "",
) -> ShippingQuote:
    """Opções de frete (manual §5) para o valor do pedido e a cidade de destino."""
    special = needs_special_freight(category_id, product_name)
    notes = ["Todos os envios têm seguro contra extravio e danos."]
    if is_metro(city):
        free = order_value >= FREE_SHIPPING_MIN
        notes.append("Entrega por motoboy próprio; a loja liga antes de entregar.")
        if not free:
            notes.append(
                f"Frete grátis a partir de {format_brl(FREE_SHIPPING_MIN)} em Campo Grande."
            )
        return ShippingQuote(
            metro=True,
            options=[
                ShippingOption("Motoboy (Campo Grande)", "1 a 3 dias úteis",
                               0.0 if free else METRO_FEE)
            ],
            special_freight=special,
            notes=notes,
        )  # fmt: skip
    notes.append("O valor é calculado pelo CEP de destino, peso e dimensões do produto.")
    notes.append("Em caso de avaria, recuse o recebimento e fale com a loja imediatamente.")
    if special:
        notes.append(
            "Instrumento de grande porte: pode exigir frete especial com cotação individual."
        )
    return ShippingQuote(
        metro=False,
        options=[
            ShippingOption("SEDEX (Correios)", "2 a 5 dias úteis", None),
            ShippingOption("Jadlog (.package)", "3 a 8 dias úteis", None),
            ShippingOption("PAC (Correios)", "5 a 12 dias úteis", None),
        ],
        special_freight=special,
        notes=notes,
    )
