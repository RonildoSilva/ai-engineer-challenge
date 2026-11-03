"""Adaptador Postgres do `Repository` (perfil de escala) e a carga a partir do pipeline.

- Mesma interface e mesma suíte de contrato do SQLite (`tests/contract/`).
- Somente leitura na consulta (`default_transaction_read_only`), SQL sempre parametrizado.
- Uma conexão por thread, como no SQLite (o agente consulta a partir de threads de trabalho);
  em produção, um pool (PgBouncer ou `psycopg_pool`) ocupa esse papel.
- `load_from_pipeline` copia o store.db e o vectors.db gerados pelo pipeline para o Postgres numa
  transação única: quem consulta nunca vê uma carga pela metade.

Requer o extra `scale` (`uv sync --extra scale`).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from importlib import resources
from pathlib import Path
from typing import Any

from emporio.domain.models import Category, Customer, Order, OrderItem, Product, Promotion

_PRODUCT_SQL = """
SELECT p.*, c.name AS category_name
FROM products p JOIN categories c USING (category_id)
"""


def _psycopg():
    try:
        import psycopg  # noqa: PLC0415 (dependência opcional do perfil de escala)
        from psycopg.rows import dict_row  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - depende do ambiente
        raise RuntimeError("backend postgres requer o extra 'scale': uv sync --extra scale") from e
    return psycopg, dict_row


def connect_read_only(dsn: str, **kwargs: Any):
    """Conexão somente leitura, preservando as `options` da URL (ex.: `-c search_path=…`)."""
    psycopg, _ = _psycopg()
    from psycopg.conninfo import conninfo_to_dict  # noqa: PLC0415

    options = str(conninfo_to_dict(dsn).get("options") or "")
    return psycopg.connect(dsn, autocommit=True,
                           options=f"{options} -c default_transaction_read_only=on".strip(),
                           **kwargs)  # fmt: skip


class PostgresRepository:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._local = threading.local()

    @property
    def _con(self):
        con = getattr(self._local, "con", None)
        if con is None or con.closed:
            _, dict_row = _psycopg()
            con = connect_read_only(self._dsn, row_factory=dict_row)
            self._local.con = con
        return con

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return self._con.execute(sql, list(params)).fetchall()

    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None

    # ------------------------------------------------------------------ catálogo

    def list_categories(self) -> list[Category]:
        rows = self._all(
            """SELECT c.category_id, c.name, c.description, count(p.product_id) AS n
               FROM categories c LEFT JOIN products p USING (category_id)
               GROUP BY c.category_id ORDER BY c.category_id"""
        )
        return [Category(r["category_id"], r["name"], r["description"], r["n"]) for r in rows]

    def get_product(self, product_id: int) -> Product | None:
        rows = self._all(_PRODUCT_SQL + " WHERE p.product_id = %s", [product_id])
        return _product(rows[0]) if rows else None

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
            where.append("p.category_id = %s")
            params.append(category_id)
        if min_price is not None:
            where.append(f"{effective} >= %s")
            params.append(min_price)
        if max_price is not None:
            where.append(f"{effective} <= %s")
            params.append(max_price)
        if availability is not None:
            where.append("p.availability = ANY(%s)")
            params.append(list(availability))
        sql = _PRODUCT_SQL + (" WHERE " + " AND ".join(where) if where else "")
        sql += f" ORDER BY {effective}, p.product_id"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
        return [_product(r) for r in self._all(sql, params)]

    # ------------------------------------------------------------------ pedidos e clientes

    def get_order(self, order_id: int) -> Order | None:
        rows = self._all("SELECT * FROM orders WHERE order_id = %s", [order_id])
        if not rows:
            return None
        o = rows[0]
        items = self._all(
            """SELECT i.product_id, p.name, i.quantity, i.unit_price_brl
               FROM order_items i JOIN products p USING (product_id)
               WHERE i.order_id = %s ORDER BY i.product_id""",
            [order_id],
        )
        return Order(
            order_id=o["order_id"],
            customer_id=o["customer_id"],
            order_date=o["order_date"],
            status=o["status"],
            total_brl=o["total_brl"],
            payment_type=o["payment_type"],
            installments=o["installments"],
            tracking_code=o["tracking_code"],
            estimated_delivery=o["estimated_delivery"],
            notes=o["notes"],
            items=tuple(
                OrderItem(i["product_id"], i["name"], i["quantity"], i["unit_price_brl"])
                for i in items
            ),
        )

    def get_customer(self, customer_id: int) -> Customer | None:
        rows = self._all(
            "SELECT customer_id, first_name, phone_digits, email, city, is_metro "
            "FROM customers WHERE customer_id = %s",
            [customer_id],
        )
        if not rows:
            return None
        c = rows[0]
        return Customer(c["customer_id"], c["first_name"], c["phone_digits"], c["email"],
                        c["city"], bool(c["is_metro"]))  # fmt: skip


def _product(r: dict[str, Any]) -> Product:
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
        specs=dict(r["specs"]),
        promotion=promo,
    )


# ------------------------------------------------------------------------------ carga

# tabela → colunas, na ordem de carga (respeita as FKs; a FK circular é adiada)
_TABLES: dict[str, tuple[str, ...]] = {
    "meta": ("key", "value"),
    "categories": ("category_id", "name", "description"),
    "products": (
        "product_id", "name", "name_normalized", "search_text", "category_id", "price_brl",
        "stock_quantity", "status", "availability", "description", "description_raw",
        "description_reliable", "specs_json", "created_at", "promo_id", "promo_percent",
        "promo_label", "promo_price_brl",
    ),
    "promotions": ("promotion_id", "product_id", "discount_percent", "description", "is_active"),
    "customers": ("customer_id", "name", "first_name", "phone", "phone_digits", "email", "city",
                  "is_metro"),
    "orders": (
        "order_id", "customer_id", "order_date", "status", "total_brl", "payment_method",
        "payment_type", "installments", "tracking_code", "estimated_delivery", "notes",
    ),
    "order_items": ("order_id", "product_id", "quantity", "unit_price_brl"),
}  # fmt: skip
_BOOL = {"description_reliable", "is_active", "is_metro"}


def _pg_columns(table: str) -> list[str]:
    return ["specs" if c == "specs_json" else c for c in _TABLES[table]]


def load_from_pipeline(dsn: str, store_db: Path, vectors_db: Path | None = None) -> dict[str, int]:
    """Recria o esquema e copia as tabelas (e os vetores) numa transação. Devolve as contagens."""
    psycopg, _ = _psycopg()
    schema = resources.files("emporio.adapters").joinpath("postgres_schema.sql").read_text("utf-8")
    src = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    counts: dict[str, int] = {}
    try:
        with psycopg.connect(dsn) as con, con.transaction():
            con.execute(schema)
            for table, cols in _TABLES.items():
                rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()  # noqa: S608 (nomes fixos deste módulo)
                rows = [_convert(cols, r) for r in rows]
                pg_cols = _pg_columns(table)
                marks = ", ".join(["%s"] * len(pg_cols))
                with con.cursor() as cur:
                    cur.executemany(
                        f"INSERT INTO {table} ({', '.join(pg_cols)}) VALUES ({marks})",  # noqa: S608
                        rows,
                    )
                counts[table] = len(rows)
            if vectors_db is not None and vectors_db.is_file():
                from emporio.adapters.pgvector import load_vectors  # noqa: PLC0415

                counts["vectors"] = load_vectors(con, vectors_db)
    finally:
        src.close()
    return counts


def _convert(cols: tuple[str, ...], row: tuple) -> tuple:
    """Valores do SQLite → tipos do Postgres (0/1 → booleano; JSON validado, ordem preservada)."""
    return tuple(_value(col, v) for col, v in zip(cols, row, strict=True))


def _value(col: str, value: Any) -> Any:
    if value is None:
        return None
    if col in _BOOL:
        return bool(value)
    if col == "specs_json":
        return json.dumps(json.loads(value), ensure_ascii=False)
    return value
