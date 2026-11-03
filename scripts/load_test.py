"""Teste de carga do servidor MCP via Streamable HTTP (a "API" da loja).

Simula N clientes simultâneos, cada um com sua sessão MCP, fazendo chamadas variadas às tools
(catálogo, detalhe, pedido com verificação, política, pagamento). Mede vazão, latência p50/p95/
p99 e erros. Sem LLM: o objetivo é medir a camada de dados e o servidor, que é o que escala
horizontalmente (o LLM escala à parte).

Uso:
    uv run python scripts/load_test.py --url http://127.0.0.1:8000/mcp   # SQLite (compose padrão)
    uv run python scripts/load_test.py --url http://127.0.0.1:8001/mcp   # Postgres (profile scale)
    uv run python scripts/load_test.py --clients 20 --calls 25 --out resultado.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from mcp import Client

# Chamadas representativas do atendimento (todas somente leitura).
CALLS: list[tuple[str, dict]] = [
    ("buscar_produtos", {"texto": "violões até R$1000"}),
    ("detalhar_produto", {"produto": "Takamine GD20"}),
    ("consultar_pedido", {"numero_pedido": "8", "email_ou_telefone": "(67) 99654-7821"}),
    ("consultar_politica", {"pergunta": "posso devolver?"}),
    ("simular_pagamento", {"valor": 2199, "parcelas": 10}),
    ("buscar_produtos", {"texto": "ukulele para criança"}),
    ("status_loja", {}),
    ("calcular_frete", {"valor_pedido": 600, "cidade": "Campo Grande"}),
]


# buscas semânticas: cada chamada embeda a pergunta no servidor de embeddings (CPU)
USES_EMBEDDINGS = {"buscar_produtos", "consultar_politica"}


async def client(url: str, calls: int, offset: int, latencies: list[float], errors: list[str]):
    async with Client(url) as c:
        for i in range(calls):
            name, args = CALLS[(offset + i) % len(CALLS)]
            t0 = time.perf_counter()
            try:
                r = await c.call_tool(name, args)
                if r.is_error:
                    errors.append(f"{name}: erro da tool")
            except Exception as e:  # noqa: BLE001 (o teste de carga conta qualquer falha)
                errors.append(f"{name}: {type(e).__name__}: {e}")
            latencies.append((time.perf_counter() - t0) * 1000)


def pct(values: list[float], p: int) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[p - 1] if len(values) > 1 else 0


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    ap.add_argument("--clients", type=int, default=10, help="sessões simultâneas")
    ap.add_argument("--calls", type=int, default=20, help="chamadas por sessão")
    ap.add_argument("--out", type=Path, help="grava o resultado em JSON")
    ap.add_argument(
        "--sem-embeddings", action="store_true",
        help="só tools que não consultam o servidor de embeddings (isola o banco)",
    )  # fmt: skip
    args = ap.parse_args()

    if args.sem_embeddings:
        CALLS[:] = [c for c in CALLS if c[0] not in USES_EMBEDDINGS]
    latencies: list[float] = []
    errors: list[str] = []
    t0 = time.perf_counter()
    await asyncio.gather(*(client(args.url, args.calls, i, latencies, errors)
                           for i in range(args.clients)))  # fmt: skip
    elapsed = time.perf_counter() - t0
    total = len(latencies)
    result = {
        "url": args.url,
        "tools": sorted({c[0] for c in CALLS}),
        "clientes": args.clients,
        "chamadas": total,
        "erros": len(errors),
        "duracao_s": round(elapsed, 2),
        "vazao_por_s": round(total / elapsed, 1),
        "latencia_ms": {
            "p50": round(pct(latencies, 50), 1),
            "p95": round(pct(latencies, 95), 1),
            "p99": round(pct(latencies, 99), 1),
            "max": round(max(latencies), 1) if latencies else 0,
        },
        "exemplos_de_erro": errors[:5],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
