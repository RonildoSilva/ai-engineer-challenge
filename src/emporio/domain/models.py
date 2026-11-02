"""Modelos de domínio (imutáveis), independentes de onde os dados estão armazenados."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Category:
    category_id: int
    name: str
    description: str | None
    product_count: int


@dataclass(frozen=True)
class Promotion:
    promotion_id: int
    percent: int
    label: str
    price_brl: float  # preço promocional


@dataclass(frozen=True)
class Product:
    product_id: int
    name: str
    name_normalized: str
    search_text: str
    category_id: int
    category_name: str
    price_brl: float
    stock_quantity: int
    status: str
    availability: str  # disponivel | esgotado | descontinuado | em_breve
    description: str | None  # None quando não confiável
    specs: dict[str, str]
    promotion: Promotion | None

    @property
    def is_sellable(self) -> bool:
        return self.availability == "disponivel"


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    product_name: str
    quantity: int
    unit_price_brl: float


@dataclass(frozen=True)
class Order:
    order_id: int
    customer_id: int
    order_date: date
    status: str
    total_brl: float
    payment_type: str
    installments: int
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None
    items: tuple[OrderItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Customer:
    """Dado pessoal: usado só para verificação de identidade, nunca listado (LGPD)."""

    customer_id: int
    first_name: str
    phone_digits: str
    email: str
    city: str
    is_metro: bool
