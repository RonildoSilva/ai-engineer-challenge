"""Etapa `snapshot_raw`: inventário das fontes brutas (início da linhagem).

Registra cada arquivo de `data/raw` com tamanho, SHA-256 e, nos CSVs, cabeçalho e número de
linhas. Falha se uma fonte esperada estiver faltando. As etapas seguintes dependem desta,
então qualquer mudança nos dados brutos invalida tudo que vem depois.
"""

from __future__ import annotations

import csv
import hashlib
import json
from typing import Any

from emporio.pipeline.core import StageContext

EXPECTED_SOURCES = (
    "categories.csv",
    "products.csv",
    "promotions.csv",
    "customers.csv",
    "orders.csv",
    "order_items.csv",
    "politicas_da_loja.pdf",
)


def snapshot_raw(ctx: StageContext) -> dict[str, Any]:
    raw_dir = ctx.inputs[0]
    missing = [name for name in EXPECTED_SOURCES if not (raw_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"fontes ausentes em {raw_dir}: {missing}")

    files: dict[str, dict[str, Any]] = {}
    for path in sorted(p for p in raw_dir.iterdir() if p.is_file()):
        info: dict[str, Any] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if path.suffix == ".csv":
            with path.open(newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                info["columns"] = next(reader)
                info["rows"] = sum(1 for _ in reader)
        files[path.name] = info

    ctx.outputs[0].write_text(json.dumps(files, indent=2, ensure_ascii=False, sort_keys=True))
    return {
        "files": len(files),
        "csv_rows": sum(f.get("rows", 0) for f in files.values()),
    }
