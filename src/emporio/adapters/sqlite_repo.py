"""Adaptador SQLite do `Repository`: somente leitura e consultas sempre parametrizadas."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from emporio.domain.models import Category, Customer, Order, OrderItem, Product, Promotion

_PRODUCT_SQL = """
SELECT p.*, c.name AS category_name
FROM products p JOIN categories c USING (category_id)
"""


class SqliteRepository:
    def __init__(self, db_path: Path):
        if not Path(db_path).is_file():
            raise FileNotFoundError(f"{db_path} não existe: rode `emporio pipeline run`")
        self._uri = f"file:{Path(db_path).resolve()}?mode=ro"
        self._local = threading.local()  # uma conexão por thread (sqlite3 não é thread-safe)

    @property
    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self._uri, uri=True)
            con.row_factory = sqlite3.Row
            self._local.con = con
        return con

    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None

    # ------------------------------------------------------------------ catálogo

    def list_categories(self) -> list[Category]:
        rows = self._con.execute(
            """SELECT c.category_id, c.name, c.description, count(p.product_id) AS n
               FROM categories c LEFT JOIN products p USING (category_id)
               GROUP BY c.category_id ORDER BY c.category_id"""
        ).fetchall()
        return [Category(r["category_id"], r["name"], r["description"], r["n"]) for r in rows]

    def get_product(self, product_id: int) -> Product | None:
        row = self._con.execute(_PRODUCT_SQL + " WHERE p.product_id = ?", (product_id,)).fetchone()
        return _product(row) if row else None

    def list_products(
        self,
        *,
        category_id: int | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        availability: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[Product]:
        effective = "coalesce(p.promo_price_brl, p.price_brl)"
        where: list[str] = []
        params: list[Any] = []
        if category_id is not None:
            where.append("p.category_id = ?")
            params.append(category_id)
        if min_price is not None:
            where.append(f"{effective} >= ?")
            params.append(min_price)
        if max_price is not None:
            where.append(f"{effective} <= ?")
            params.append(max_price)
        if availability is not None:
            values = list(availability)
            where.append(f"p.availability IN ({', '.join('?' * len(values))})")
            params.extend(values)
        sql = _PRODUCT_SQL + (" WHERE " + " AND ".join(where) if where else "")
        sql += f" ORDER BY {effective}, p.product_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [_product(r) for r in self._con.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ pedidos e clientes

    def get_order(self, order_id: int) -> Order | None:
        o = self._con.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if o is None:
            return None
        items = self._con.execute(
            """SELECT i.product_id, p.name, i.quantity, i.unit_price_brl
               FROM order_items i JOIN products p USING (product_id)
               WHERE i.order_id = ? ORDER BY i.product_id""",
            (order_id,),
        ).fetchall()
        return Order(
            order_id=o["order_id"],
            customer_id=o["customer_id"],
            order_date=date.fromisoformat(o["order_date"]),
            status=o["status"],
            total_brl=o["total_brl"],
            payment_type=o["payment_type"],
            installments=o["installments"],
            tracking_code=o["tracking_code"],
            estimated_delivery=(
                date.fromisoformat(o["estimated_delivery"]) if o["estimated_delivery"] else None
            ),
            notes=o["notes"],
            items=tuple(OrderItem(i[0], i[1], i[2], i[3]) for i in items),
        )

    def get_customer(self, customer_id: int) -> Customer | None:
        c = self._con.execute(
            "SELECT customer_id, first_name, phone_digits, email, city, is_metro "
            "FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if c is None:
            return None
        return Customer(
            c["customer_id"], c["first_name"], c["phone_digits"], c["email"], c["city"],
            bool(c["is_metro"]),
        )  # fmt: skip


def _product(r: sqlite3.Row) -> Product:
    promo = (
        Promotion(r["promo_id"], r["promo_percent"], r["promo_label"], r["promo_price_brl"])
        if r["promo_id"] is not None
        else None
    )
    return Product(
        product_id=r["product_id"],
        name=r["name"],
        name_normalized=r["name_normalized"],
        search_text=r["search_text"],
        category_id=r["category_id"],
        category_name=r["category_name"],
        price_brl=r["price_brl"],
        stock_quantity=r["stock_quantity"],
        status=r["status"],
        availability=r["availability"],
        description=r["description"],
        specs=json.loads(r["specs_json"]),
        promotion=promo,
    )
