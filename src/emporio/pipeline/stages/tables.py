"""Etapas da camada de dados: validate_raw → transform → load → quality_checks.

- validate_raw: contrato de cada CSV (tipos, domínios, formatos) + integridade referencial.
- transform:    aplica as decisões de docs/data_report.md e grava tabelas de staging (JSONL).
- load:         cria o store.db a partir do schema.sql (com CHECKs) de forma atômica.
- quality_checks: verifica invariantes no banco carregado (falha se algo violar).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from emporio.data import rules
from emporio.data.schemas import TABLES, read_all
from emporio.pipeline.core import StageContext

STAGING_TABLES = ("categories", "products", "promotions", "customers", "orders", "order_items")


def raw_paths(raw_dir: Path) -> tuple[Path, ...]:
    return tuple(raw_dir / f"{name}.csv" for name in TABLES)


# ------------------------------------------------------------------------------ validate


def validate_raw(ctx: StageContext) -> dict[str, Any]:
    raw_dir = ctx.inputs[0].parent
    data = read_all(raw_dir)  # levanta TableValidationError com todas as falhas
    report = {name: len(rows) for name, rows in data.items()}
    ctx.outputs[0].write_text(json.dumps({"rows": report, "errors": []}, indent=2))
    return report


# ------------------------------------------------------------------------------ transform


def transform(ctx: StageContext) -> dict[str, Any]:
    data = read_all(ctx.inputs[0].parent)
    categories = {c.category_id: c for c in data["categories"]}

    active_promos: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for p in data["promotions"]:
        if p.is_active:
            active_promos[p.product_id].append((p.promotion_id, p.discount_percent, p.description))

    products = []
    unreliable = []
    for p in data["products"]:
        reliable = rules.description_is_reliable(p.name, p.description)
        if not reliable:
            unreliable.append(p.product_id)
        promo = rules.current_promotion(active_promos.get(p.product_id, []))
        category = categories[p.category_id].name
        products.append(
            {
                "product_id": p.product_id,
                "name": p.name,
                "name_normalized": rules.normalize(p.name),
                "search_text": rules.search_text(
                    p.name, category, p.specs, p.description if reliable else None
                ),
                "category_id": p.category_id,
                "price_brl": p.price_brl,
                "stock_quantity": p.stock_quantity,
                "status": p.status,
                "availability": rules.availability(p.status, p.stock_quantity),
                "description": p.description if reliable else None,
                "description_raw": p.description,
                "description_reliable": int(reliable),
                "specs_json": json.dumps(p.specs, ensure_ascii=False, sort_keys=True),
                "created_at": p.created_at.isoformat(),
                "promo_id": promo[0] if promo else None,
                "promo_percent": promo[1] if promo else None,
                "promo_label": promo[2] if promo else None,
                "promo_price_brl": rules.promo_price(p.price_brl, promo[1]) if promo else None,
            }
        )

    price = {p.product_id: p.price_brl for p in data["products"]}
    tables: dict[str, list[dict[str, Any]]] = {
        "categories": [c.model_dump() for c in data["categories"]],
        "products": products,
        "promotions": [p.model_dump() for p in data["promotions"]],
        "customers": [
            {
                "customer_id": c.customer_id,
                "name": c.name,
                "first_name": rules.first_name(c.name),
                "phone": c.phone,
                "phone_digits": rules.digits(c.phone),
                "email": c.email.lower(),
                "city": c.city,
                "is_metro": int(rules.is_metro(c.city)),
            }
            for c in data["customers"]
        ],
        "orders": [
            {
                **o.model_dump(mode="json"),
                **dict(
                    zip(
                        ("payment_type", "installments"),
                        rules.parse_payment(o.payment_method),
                        strict=True,
                    )
                ),
            }
            for o in data["orders"]
        ],
        "order_items": [
            {**i.model_dump(), "unit_price_brl": price[i.product_id]} for i in data["order_items"]
        ],
    }

    out_dir = ctx.outputs[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    by_availability: dict[str, int] = defaultdict(int)
    for p in products:
        by_availability[p["availability"]] += 1
    return {
        "products": len(products),
        "availability": dict(sorted(by_availability.items())),
        "unreliable_descriptions": unreliable,
        "active_promotions": sum(1 for p in products if p["promo_id"]),
    }


# ------------------------------------------------------------------------------ load


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load(ctx: StageContext) -> dict[str, Any]:
    staging = ctx.inputs[0]
    db_path = ctx.outputs[0]
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)

    con = sqlite3.connect(tmp)
    try:
        schema = files("emporio.data").joinpath("schema.sql").read_text(encoding="utf-8")
        con.executescript(schema)
        counts: dict[str, int] = {}
        with con:  # uma transação: tudo ou nada (FKs adiadas verificadas no commit)
            for name in STAGING_TABLES:
                rows = _read_jsonl(staging / f"{name}.jsonl")
                if rows:
                    cols = list(rows[0])
                    # nomes de tabela/coluna vêm de constantes e do próprio staging, não do usuário;
                    # os valores sempre vão como parâmetros
                    sql = (
                        f"INSERT INTO {name} ({', '.join(cols)}) "  # noqa: S608
                        f"VALUES ({', '.join('?' * len(cols))})"
                    )
                    con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
                counts[name] = len(rows)
            con.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [
                    ("built_at", datetime.now(UTC).isoformat()),
                    ("schema_version", "1"),
                    ("row_counts", json.dumps(counts)),
                ],
            )
        violations = con.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"violações de chave estrangeira: {violations[:5]}")
        con.execute("VACUUM")
    finally:
        con.close()
    tmp.replace(db_path)  # troca atômica: leitores nunca veem um banco pela metade
    return counts


# ------------------------------------------------------------------------------ quality

# (descrição, SQL que deve retornar 0)
QUALITY_CHECKS: tuple[tuple[str, str], ...] = (
    ("produtos vendáveis precisam ser active com estoque",
     ("SELECT count(*) FROM products WHERE availability='disponivel' "
      "AND NOT (status='active' AND stock_quantity>0)")),
    ("no máximo uma promoção vigente por produto, e ela precisa estar ativa",
     ("SELECT count(*) FROM products p LEFT JOIN promotions r ON r.promotion_id=p.promo_id "
      "WHERE p.promo_id IS NOT NULL AND (r.is_active<>1 OR r.product_id<>p.product_id)")),
    ("produto com promoção ativa sem promoção vigente",
     ("SELECT count(*) FROM promotions r JOIN products p USING (product_id) "
      "WHERE r.is_active=1 AND p.promo_id IS NULL")),
    ("preço promocional coerente com o percentual",
     ("SELECT count(*) FROM products WHERE promo_id IS NOT NULL "
      "AND abs(promo_price_brl - round(price_brl*(1-promo_percent/100.0), 2)) > 0.005")),
    ("pedido sem itens",
     ("SELECT count(*) FROM orders o WHERE NOT EXISTS "
      "(SELECT 1 FROM order_items i WHERE i.order_id=o.order_id)")),
    ("descrição exposta apesar de não confiável",
     "SELECT count(*) FROM products WHERE description_reliable=0 AND description IS NOT NULL"),
    ("texto de busca vazio",
     "SELECT count(*) FROM products WHERE trim(search_text)=''"),
)  # fmt: skip


def quality_checks(ctx: StageContext) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{ctx.inputs[0]}?mode=ro", uri=True)
    try:
        failures = {
            desc: n for desc, sql in QUALITY_CHECKS if (n := con.execute(sql).fetchone()[0])
        }
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    report = {"checks": len(QUALITY_CHECKS), "failures": failures, "integrity": integrity}
    ctx.outputs[0].write_text(json.dumps(report, indent=2, ensure_ascii=False))
    if failures or integrity != "ok":
        raise AssertionError(f"checagens de qualidade falharam: {failures or integrity}")
    return {"checks_passed": len(QUALITY_CHECKS)}
