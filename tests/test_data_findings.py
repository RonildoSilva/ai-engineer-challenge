"""Garante que as afirmações de docs/data_report.md continuam verdadeiras sobre data/raw.

Se os dados ou o relatório mudarem, estes testes acusam a divergência.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


@pytest.fixture(scope="module")
def d() -> dict[str, pd.DataFrame]:
    names = ["categories", "products", "promotions", "customers", "orders", "order_items"]
    return {n: pd.read_csv(RAW / f"{n}.csv") for n in names}


def test_explore_script_runs():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "explore_data.py")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "marca do nome ≠ marca da descrição (7)" in result.stdout


def test_table_sizes(d):
    sizes = {k: len(v) for k, v in d.items()}
    assert sizes == {
        "categories": 9,
        "products": 65,
        "promotions": 25,
        "customers": 50,
        "orders": 20,
        "order_items": 22,
    }


def test_referential_integrity(d):
    pids = set(d["products"]["product_id"])
    assert set(d["promotions"]["product_id"]) <= pids
    assert set(d["order_items"]["product_id"]) <= pids
    assert set(d["orders"]["customer_id"]) <= set(d["customers"]["customer_id"])
    assert set(d["products"]["category_id"]) <= set(d["categories"]["category_id"])


def test_empty_categories(d):
    used = set(d["products"]["category_id"])
    assert set(d["categories"]["category_id"]) - used == {6, 7, 8}


def test_status_stock_conflicts(d):
    p = d["products"].set_index("product_id")
    assert (p.loc[96, "status"], p.loc[96, "stock_quantity"]) == ("active", 0)
    assert (p.loc[113, "status"], p.loc[113, "stock_quantity"]) == ("discontinued", 0)
    assert (p.loc[130, "status"], p.loc[130, "stock_quantity"]) == ("coming_soon", 2)
    assert (p.loc[136, "status"], p.loc[136, "stock_quantity"]) == ("discontinued", 13)
    assert set(p.index[p["status"] != "active"]) == {113, 130, 136}


def test_coming_soon_product_already_sold(d):
    oi = d["order_items"]
    assert set(oi.loc[oi["product_id"] == 130, "order_id"]) == {3, 14}


def test_active_promotions(d):
    active = d["promotions"].query("is_active == 1")
    assert dict(zip(active["product_id"], active["discount_percent"], strict=True)) == {
        127: 10,
        121: 20,
        90: 18,
        94: 8,
    }
    assert "start_date" not in d["promotions"].columns  # sem vigência por data


def test_takamine_gd20_has_no_active_promotion(d):
    p = d["products"].set_index("product_id")
    assert p.loc[95, "name"].startswith("Takamine GD20") and p.loc[95, "price_brl"] == 2199
    promos = d["promotions"].query("product_id == 95")
    assert not promos["is_active"].any()


def test_order_total_mismatches(d):
    items = d["order_items"].merge(d["products"][["product_id", "price_brl"]], on="product_id")
    items["sub"] = items["price_brl"] * items["quantity"]
    soma = items.groupby("order_id")["sub"].sum()
    diff = (d["orders"].set_index("order_id")["total_brl"] - soma).round(2)
    assert diff[diff != 0].to_dict() == {3: -48.0, 20: -88.0}


def test_orders_period_and_tracking(d):
    o = d["orders"]
    assert (o["order_date"].min(), o["order_date"].max()) == ("2025-10-15", "2026-03-22")
    codes = o["tracking_code"].dropna()
    assert len(codes) == 8 and codes.str.fullmatch(r"BR[A-Z0-9]{9}BR").all()
    shipped = o["status"].isin(["shipped", "delivered"])
    assert o.loc[shipped, "tracking_code"].notna().all()
    assert o.loc[~shipped, "tracking_code"].isna().all()


def test_duplicated_customer_email(d):
    c = d["customers"]
    dup = c[c.duplicated("email", keep=False)]
    assert set(dup["customer_id"]) == {11, 43}
    assert not c["phone"].duplicated().any()
