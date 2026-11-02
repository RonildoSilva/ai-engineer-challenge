"""Camada de dados: regras puras, contratos dos CSVs, restrições do esquema e etapas."""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from emporio.data import rules
from emporio.data.schemas import TableValidationError, read_all
from emporio.pipeline.stages import build_pipeline
from tests.conftest import ROOT, make_settings

# ============================================================================ regras puras


@pytest.mark.parametrize(
    ("status", "stock", "expected"),
    [
        ("active", 5, "disponivel"),
        ("active", 0, "esgotado"),
        ("discontinued", 13, "descontinuado"),  # 136: status prevalece sobre estoque
        ("discontinued", 0, "descontinuado"),
        ("coming_soon", 2, "em_breve"),  # 130
    ],
)
def test_availability(status, stock, expected):
    assert rules.availability(status, stock) == expected


@pytest.mark.parametrize(
    ("name", "description", "reliable"),
    [
        ("Music Man Bass 1X", "Contrabaixo Fender Jazz Bass com corpo em Alder", False),
        ("Ibanez Bass 2X", "Contrabaixo Precision de 4 cordas", False),  # modelo → Fender
        ("Music Man Bass 4X", "Contrabaixo Music Man StingRay", True),
        ("Yamaha C40", "Violão clássico com tampo em Spruce", True),  # sem marca na descrição
        ("Yamaha C40", None, False),
    ],
)
def test_description_reliability(name, description, reliable):
    assert rules.description_is_reliable(name, description) is reliable


def test_normalize():
    assert (
        rules.normalize("Giannini GN-15 Nylon Cedr Natural") == "giannini gn 15 nylon cedro natural"
    )
    assert rules.normalize("Violão  Elétrico") == "violao eletrico"


def test_search_text_skips_unreliable_description():
    assert "jazz" not in rules.search_text("Music Man", "Baixos", {"x": "y"}, None)
    assert "spruce" in rules.search_text("Yamaha", "Violões", {"top": "Spruce"}, None)


def test_current_promotion_picks_highest_discount_then_oldest():
    assert rules.current_promotion([]) is None
    assert rules.current_promotion([(3, 10, "a"), (1, 25, "b"), (2, 25, "c")]) == (1, 25, "b")


@pytest.mark.parametrize(
    ("method", "expected"),
    [("pix", ("pix", 1)), ("debit", ("debito", 1)), ("boleto", ("boleto", 1)),
     ("credit_3x", ("credito", 3)), ("credit_12x", ("credito", 12))],
)  # fmt: skip
def test_parse_payment(method, expected):
    assert rules.parse_payment(method) == expected


def test_customer_helpers():
    assert rules.digits("(67) 99812-3456") == "67998123456"
    assert rules.first_name("Ana Carolina Ferreira") == "Ana"
    assert rules.is_metro("Campo Grande")
    assert not rules.is_metro("Três Lagoas")


def test_all_seven_divergent_descriptions_detected():
    with (ROOT / "data/raw/products.csv").open(encoding="utf-8") as f:
        flagged = {
            int(r["product_id"])
            for r in csv.DictReader(f)
            if not rules.description_is_reliable(r["name"], r["description"])
        }
    assert flagged == {135, 136, 137, 139, 140, 142, 144}


# ============================================================================ contratos dos CSVs


@pytest.fixture
def raw_copy(tmp_path) -> Path:
    raw = tmp_path / "raw"
    shutil.copytree(ROOT / "data/raw", raw)
    return raw


def _edit_csv(path: Path, row_index: int, **changes) -> None:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0])
    rows[row_index].update(changes)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_real_data_is_valid():
    data = read_all(ROOT / "data/raw")
    assert {k: len(v) for k, v in data.items()}["products"] == 65


@pytest.mark.parametrize(
    ("table", "changes", "message"),
    [
        ("products", {"price_brl": "-10"}, "products.csv:2 price_brl"),
        ("products", {"status": "esgotado"}, "products.csv:2 status"),
        ("products", {"specs": "{quebrado"}, "specs não é JSON válido"),
        ("orders", {"payment_method": "cheque"}, "orders.csv:2 payment_method"),
        ("orders", {"tracking_code": "XX123"}, "orders.csv:2 tracking_code"),
        ("customers", {"email": "sem-arroba"}, "customers.csv:2 email"),
        ("promotions", {"discount_percent": "150"}, "promotions.csv:2 discount_percent"),
    ],
)
def test_invalid_rows_are_reported_with_location(raw_copy, table, changes, message):
    _edit_csv(raw_copy / f"{table}.csv", 0, **changes)
    with pytest.raises(TableValidationError) as exc:
        read_all(raw_copy)
    assert any(message in e for e in exc.value.errors), exc.value.errors


def test_referential_integrity_is_checked(raw_copy):
    _edit_csv(raw_copy / "order_items.csv", 0, product_id="99999")
    with pytest.raises(TableValidationError, match=r"order_items\.product_id"):
        read_all(raw_copy)


def test_unexpected_columns_are_reported(raw_copy):
    text = (raw_copy / "categories.csv").read_text().replace("description", "descricao", 1)
    (raw_copy / "categories.csv").write_text(text)
    with pytest.raises(TableValidationError, match="colunas inesperadas"):
        read_all(raw_copy)


# ============================================================================ esquema (CHECKs)


@pytest.fixture
def writable_db(built_store, tmp_path) -> Iterator[sqlite3.Connection]:
    db = tmp_path / "w.db"
    shutil.copy(built_store, db)
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    yield con
    con.close()


@pytest.mark.parametrize(
    "sql",
    [
        # vendável com estoque zero
        "UPDATE products SET stock_quantity = 0 WHERE product_id = 95",
        # descontinuado marcado como disponível
        "UPDATE products SET availability = 'disponivel' WHERE product_id = 136",
        # descrição exposta apesar de não confiável
        "UPDATE products SET description = 'x' WHERE product_id = 135",
        # preço promocional maior que o cheio
        "UPDATE products SET promo_price_brl = 99999 WHERE product_id = 94",
        # pedido pendente com rastreio
        "UPDATE orders SET tracking_code = 'BRAB1234567BR' WHERE order_id = 13",
        # parcelas em pagamento à vista
        "UPDATE orders SET installments = 3 WHERE order_id = 1",
        # item de produto inexistente
        "INSERT INTO order_items VALUES (1, 99999, 1, 10)",
    ],
)
def test_schema_rejects_rule_violations(writable_db, sql):
    with pytest.raises(sqlite3.IntegrityError):
        writable_db.execute(sql)


def test_db_metadata(built_store):
    con = sqlite3.connect(built_store)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    assert meta["schema_version"] == "1"
    assert json.loads(meta["row_counts"])["orders"] == 20
    view = con.execute("SELECT count(*) FROM orders_with_items").fetchone()[0]
    assert view == 22
    con.close()


# ============================================================================ etapas no pipeline


def test_quality_report_written(built_store):
    report = json.loads((built_store.parent / "quality_report.json").read_text())
    assert report["failures"] == {}
    assert report["integrity"] == "ok"
    assert report["checks"] >= 7


def test_invalid_raw_blocks_load_and_keeps_previous_db(raw_copy, tmp_path):
    settings = make_settings(data_raw_dir=raw_copy, data_processed_dir=tmp_path / "p")
    assert build_pipeline(settings).run().ok
    before = settings.store_db.read_bytes()

    _edit_csv(raw_copy / "products.csv", 0, price_brl="-1")
    report = build_pipeline(settings).run().by_name()
    assert report["validate_raw"].status == "failed"
    assert report["load"].status == "skipped"
    assert settings.store_db.read_bytes() == before  # o banco anterior continua intacto


def test_rule_change_invalidates_transform(raw_copy, tmp_path, monkeypatch):
    """Mudar o código de `rules` (declarado em `code`) refaz transform e o que vem depois."""
    settings = make_settings(data_raw_dir=raw_copy, data_processed_dir=tmp_path / "p")
    build_pipeline(settings).run()
    monkeypatch.setattr(
        "emporio.pipeline.core._source",
        lambda obj: "regras alteradas" if obj is rules else __import__("inspect").getsource(obj),
    )
    report = build_pipeline(settings).run().by_name()
    assert report["validate_raw"].status == "up_to_date"
    assert report["transform"].status == "ok"
