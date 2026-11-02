"""Contratos das fontes brutas (CSVs): uma linha inválida falha o pipeline com a causa exata.

Os modelos validam tipo, domínio e formato de cada campo. Não aplicam regras de negócio,
que ficam em `rules.py`.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ProductStatus = Literal["active", "discontinued", "coming_soon"]
OrderStatus = Literal["pending", "confirmed", "shipped", "delivered", "cancelled"]


class _Raw(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if v == "" else v


class RawCategory(_Raw):
    category_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    description: str | None = None


class RawProduct(_Raw):
    product_id: int = Field(gt=0)
    price_brl: float = Field(gt=0)
    name: str = Field(min_length=1)
    category_id: int = Field(gt=0)
    description: str | None = None
    stock_quantity: int = Field(ge=0)
    status: ProductStatus
    specs: dict[str, str]
    created_at: date

    @field_validator("specs", mode="before")
    @classmethod
    def _parse_specs(cls, v: object) -> object:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"specs não é JSON válido: {e.msg}") from e
        return v


class RawPromotion(_Raw):
    promotion_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
    discount_percent: int = Field(gt=0, lt=100)
    description: str = Field(min_length=1)
    is_active: bool  # aceita apenas 0/1 (e true/false)


class RawCustomer(_Raw):
    customer_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    phone: str = Field(pattern=r"^\(\d{2}\) \d{4,5}-\d{4}$")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    city: str = Field(min_length=1)


class RawOrder(_Raw):
    order_id: int = Field(gt=0)
    customer_id: int = Field(gt=0)
    order_date: date
    status: OrderStatus
    total_brl: float = Field(ge=0)
    payment_method: str = Field(pattern=r"^(pix|debit|boleto|credit_([1-9]|1[0-2])x)$")
    tracking_code: str | None = Field(default=None, pattern=r"^BR[A-Z0-9]{9}BR$")
    estimated_delivery: date | None = None
    notes: str | None = None


class RawOrderItem(_Raw):
    order_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    product_id: int = Field(gt=0)


TABLES: dict[str, type[_Raw]] = {
    "categories": RawCategory,
    "products": RawProduct,
    "promotions": RawPromotion,
    "customers": RawCustomer,
    "orders": RawOrder,
    "order_items": RawOrderItem,
}


class TableValidationError(Exception):
    PREVIEW = 5  # quantos erros aparecem na mensagem; a lista completa fica em `errors`

    def __init__(self, errors: list[str]):
        self.errors = errors
        n = self.PREVIEW
        preview = "; ".join(errors[:n]) + (f" (+{len(errors) - n})" if len(errors) > n else "")
        super().__init__(f"{len(errors)} erro(s) de validação: {preview}")


def read_table[M: _Raw](path: Path, model: type[M]) -> tuple[list[M], list[str]]:
    """Lê um CSV validando cada linha. Devolve (linhas válidas, erros legíveis)."""
    rows: list[M] = []
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        expected = set(model.model_fields)
        got = set(reader.fieldnames or [])
        if got != expected:
            missing, extra = sorted(expected - got), sorted(got - expected)
            return [], [f"{path.name}: colunas inesperadas (faltando={missing}, extras={extra})"]
        for line_no, raw in enumerate(reader, start=2):  # linha 1 = cabeçalho
            try:
                rows.append(model.model_validate(raw))
            except ValidationError as e:
                for err in e.errors():
                    field = ".".join(map(str, err["loc"])) or "?"
                    errors.append(f"{path.name}:{line_no} {field}: {err['msg']}")
    return rows, errors


def read_all(raw_dir: Path) -> dict[str, list]:
    """Lê e valida todas as tabelas, incluindo a integridade referencial.

    Levanta `TableValidationError` com todos os problemas encontrados de uma vez.
    """
    data: dict[str, list] = {}
    errors: list[str] = []
    for name, model in TABLES.items():
        rows, errs = read_table(raw_dir / f"{name}.csv", model)
        data[name] = rows
        errors += errs
    if errors:
        raise TableValidationError(errors)
    errors += _integrity(data)
    if errors:
        raise TableValidationError(errors)
    return data


def _integrity(d: dict[str, list]) -> list[str]:
    errs: list[str] = []

    def unique(table: str, key: str) -> None:
        values = [getattr(r, key) for r in d[table]]
        dups = sorted({v for v in values if values.count(v) > 1})
        if dups:
            errs.append(f"{table}: {key} duplicado {dups}")

    def fk(table: str, key: str, ref: str, ref_key: str) -> None:
        known = {getattr(r, ref_key) for r in d[ref]}
        bad = sorted({getattr(r, key) for r in d[table]} - known)
        if bad:
            errs.append(f"{table}.{key} sem correspondente em {ref}: {bad}")

    unique("categories", "category_id")
    unique("products", "product_id")
    unique("products", "name")
    unique("promotions", "promotion_id")
    unique("customers", "customer_id")
    unique("orders", "order_id")
    fk("products", "category_id", "categories", "category_id")
    fk("promotions", "product_id", "products", "product_id")
    fk("orders", "customer_id", "customers", "customer_id")
    fk("order_items", "order_id", "orders", "order_id")
    fk("order_items", "product_id", "products", "product_id")
    pairs = [(i.order_id, i.product_id) for i in d["order_items"]]
    if len(pairs) != len(set(pairs)):
        errs.append("order_items: par (order_id, product_id) repetido")
    return errs
