"""Exploração dos dados brutos: recalcula todos os achados de docs/data_report.md.

Uso:  uv run python scripts/explore_data.py

Somente leitura: não altera data/raw. Cada seção imprime o que o relatório afirma,
para que qualquer pessoa possa conferir as inconsistências apontadas.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 70)


def title(text: str) -> None:
    print(f"\n{'=' * 4} {text} {'=' * (90 - len(text))}")


def load() -> dict[str, pd.DataFrame]:
    names = ["categories", "products", "promotions", "customers", "orders", "order_items"]
    return {n: pd.read_csv(RAW / f"{n}.csv") for n in names}


def overview(d: dict[str, pd.DataFrame]) -> None:
    title("Visão geral das tabelas")
    for name, df in d.items():
        nulls = {c: int(n) for c, n in df.isna().sum().items() if n}
        print(f"{name:12} {len(df):3} linhas | colunas: {list(df.columns)} | nulos: {nulls or '-'}")


def categories(d: dict[str, pd.DataFrame]) -> None:
    title("Produtos por categoria")
    counts = d["products"].groupby("category_id").size()
    cat = d["categories"].set_index("category_id")
    cat["produtos"] = counts.reindex(cat.index).fillna(0).astype(int)
    print(cat[["name", "produtos"]].to_string())
    vazias = cat[cat["produtos"] == 0]["name"].tolist()
    print(f"\n→ categorias sem nenhum produto: {vazias}")


def products(d: dict[str, pd.DataFrame]) -> None:
    p = d["products"]
    title("Produtos: status × estoque")
    agg = p.groupby("status").agg(n=("product_id", "size"), estoque=("stock_quantity", "sum"))
    print(agg)

    title("Produtos com status/estoque conflitante")
    conflito = p[
        ((p["status"] == "active") & (p["stock_quantity"] == 0))
        | ((p["status"] != "active") & (p["stock_quantity"] > 0))
        | (p["status"] != "active")
    ]
    print(conflito[["product_id", "name", "status", "stock_quantity"]].to_string(index=False))

    title("Nome × descrição (categorias 1–4: guitarras, baixos, baterias, teclados)")
    print(p[p["category_id"] <= 4][["product_id", "name", "description"]].to_string(index=False))
    # Marca citada no nome deve aparecer na descrição; se outra marca aparece, há divergência.
    # Modelos famosos citados sem a marca (ex.: "Precision") são mapeados para o fabricante.
    brands = [
        "Fender",
        "Gibson",
        "Ibanez",
        "PRS",
        "Music Man",
        "Yamaha",
        "Pearl",
        "Tama",
        "Korg",
        "Nord",
        "Roland",
    ]
    models = {"Precision": "Fender", "Jazz Bass": "Fender", "StingRay": "Music Man"}

    def brands_in(text: str) -> list[str]:
        found = [b for b in brands if b in text]
        return found + [brand for model, brand in models.items() if model in text]

    rows = []
    for _, r in p[p["category_id"] <= 4].iterrows():
        in_name = [b for b in brands if b in r["name"]]
        in_desc = brands_in(r["description"])
        if in_name and in_desc and not set(in_name) & set(in_desc):
            rows.append((r["product_id"], in_name[0], in_desc[0]))
    print(
        f"\n→ marca do nome ≠ marca da descrição ({len(rows)}): "
        + ", ".join(f"{pid} ({n}→{d})" for pid, n, d in rows)
    )

    title("Specs (JSON) — validade e chaves")
    keys: set[str] = set()
    invalid = []
    for pid, raw in zip(p["product_id"], p["specs"], strict=True):
        try:
            keys |= set(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            invalid.append(pid)
    print(f"JSON inválido: {invalid or 'nenhum'} | chaves: {sorted(keys)}")

    title("Faixa de preço por categoria (R$)")
    print(p.groupby("category_id")["price_brl"].describe()[["count", "min", "max"]])
    print(f"\nIDs fora de ordem no arquivo: {not p['product_id'].is_monotonic_increasing}")


def promotions(d: dict[str, pd.DataFrame]) -> None:
    pr, p = d["promotions"], d["products"]
    title("Promoções")
    print(pr.groupby("is_active").size().rename("n").to_string())
    ativas = pr[pr["is_active"] == 1].merge(p[["product_id", "name", "price_brl"]], on="product_id")
    ativas["preco_promo"] = (ativas["price_brl"] * (1 - ativas["discount_percent"] / 100)).round(2)
    print("\nPromoções ATIVAS:")
    print(
        ativas[
            [
                "promotion_id",
                "product_id",
                "name",
                "discount_percent",
                "description",
                "price_brl",
                "preco_promo",
            ]
        ].to_string(index=False)
    )
    multi = pr.groupby("product_id").size()
    print(f"\nProdutos com mais de uma promoção: {multi[multi > 1].to_dict()}")
    print(
        f"Promoções de produtos inexistentes: "
        f"{sorted(set(pr['product_id']) - set(p['product_id'])) or 'nenhuma'}"
    )
    print("Colunas de vigência (data início/fim): ausentes — só existe is_active")


def orders(d: dict[str, pd.DataFrame]) -> None:
    o, oi, p = d["orders"], d["order_items"], d["products"]
    title("Pedidos por status")
    print(
        o.groupby("status")
        .agg(n=("order_id", "size"), com_rastreio=("tracking_code", "count"))
        .to_string()
    )
    print(f"\nPeríodo: {o['order_date'].min()} a {o['order_date'].max()}")

    title("Total do pedido × soma dos itens")
    items = oi.merge(p[["product_id", "price_brl", "status"]], on="product_id")
    items["subtotal"] = items["price_brl"] * items["quantity"]
    soma = items.groupby("order_id")["subtotal"].sum().rename("soma_itens")
    chk = o.set_index("order_id")[["total_brl"]].join(soma)
    chk["diferenca"] = (chk["total_brl"] - chk["soma_itens"]).round(2)
    print(chk[chk["diferenca"].abs() > 0.01].to_string())

    title("Itens de pedidos com produtos não ativos")
    print(
        items[items["status"] != "active"][["order_id", "product_id", "status"]].to_string(
            index=False
        )
    )

    title("Formato dos códigos de rastreio (manual §5.3: BR + 9 alfanuméricos + BR)")
    codes = o["tracking_code"].dropna()
    ok = codes.str.fullmatch(r"BR[A-Z0-9]{9}BR")
    print(f"{int(ok.sum())}/{len(codes)} no formato esperado")
    print(
        f"Pedidos sem data de entrega real: coluna inexistente; "
        f"estimated_delivery preenchida em {o['estimated_delivery'].notna().sum()}/{len(o)}"
    )


def customers(d: dict[str, pd.DataFrame]) -> None:
    c, o = d["customers"], d["orders"]
    title("Clientes")
    print(c.groupby("city").size().rename("n").to_string())
    dup = c[c.duplicated("email", keep=False)]
    cols = ["customer_id", "name", "email", "phone"]
    print(f"\nE-mails duplicados:\n{dup[cols].to_string(index=False)}")
    dup_phones = c[c.duplicated("phone", keep=False)]["phone"].tolist()
    print(f"Telefones duplicados: {dup_phones or 'nenhum'}")
    sem = sorted(set(o["customer_id"]) - set(c["customer_id"]))
    print(f"Pedidos de clientes inexistentes: {sem or 'nenhum'}")


def policies() -> None:
    title("Manual de políticas (PDF) — trechos conflitantes")
    pages = PdfReader(RAW / "politicas_da_loja.pdf").pages
    raw = "\n".join(page.extract_text() or "" for page in pages)
    # pypdf extrai ~1 palavra por linha e quebra até números ("3341\n-\n4444"): normalizar.
    text = re.sub(r"\s*-\s*", "-", re.sub(r"\s+", " ", raw))
    print(f"linhas na extração bruta: {raw.count(chr(10))} (texto precisa de normalização)")
    checks = {
        "telefone (67) 3341-4444": r"3341-4444",
        "WhatsApp (67) 3321-4500": r"3321-4500",
        "e-mail sem acento": r"contato@emporiodamusica\.com\.br",
        "e-mail com acento": r"contato@empóriodamusica\.com\.br",
        "tabela: crédito parcela mín. R$100": r"Parcela mínima de R\$ 100,00",
        "§3.1: 4x–6x parcela mín. R$80": r"parcela mínima de R\$ 80,00",
        "§3.1: até 3x 'exceto abaixo de R$50'": r"exceto abaixo de R\$ 50,00",
    }
    for label, pattern in checks.items():
        print(f"{label:40} ocorrências: {len(re.findall(pattern, text))}")


def main() -> None:
    d = load()
    overview(d)
    categories(d)
    products(d)
    promotions(d)
    orders(d)
    customers(d)
    policies()


if __name__ == "__main__":
    main()
