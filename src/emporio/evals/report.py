"""Resultados da avaliação: um JSON por rodada em evals/resultados/ e um resumo em markdown.

O resumo (evals/resultados/README.md) é regenerado a partir de todos os JSON presentes, então
rodar só um perfil não apaga a linha dos outros.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def save(out_dir: Path, name: str, data: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    data = {"gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"), **data}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_summary(out_dir)
    return path


def _load(out_dir: Path, prefix: str) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(out_dir.glob(f"{prefix}*.json"))
    ]


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def write_summary(out_dir: Path) -> Path:
    lines = [
        "# Resultados da avaliação",
        "",
        "Gerado por `uv run emporio eval …` a partir dos JSON desta pasta (não editar à mão).",
        "",
    ]
    if scen := _load(out_dir, "cenarios_"):
        lines += [
            "## Cenários de ponta a ponta (evals/scenarios.yaml)",
            "",
            (
                "| Perfil | Modelo | Cenários aprovados | Falhas de asserção | Turnos com LLM "
                "| Latência p50 | Latência p95 | Prompt médio |"
            ),
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in sorted(scen, key=lambda r: (r["perfil"] != "oraculo", r["perfil"])):
            lines.append(
                f"| {r['perfil']} | {r['modelo']} | {r['aprovados']}/{r['cenarios']} "
                f"({_pct(r['taxa_aprovacao'])}) | {r['falhas_de_assercao']} | "
                f"{r['turnos_com_llm']}/{r['turnos']} | {r['latencia_llm_p50_s']} s | "
                f"{r['latencia_llm_p95_s']} s | {r['prompt_tokens_medio']} tokens |"
            )
        ids = [c["id"] for c in scen[0]["cenarios_detalhe"]]
        profiles = sorted(scen, key=lambda r: (r["perfil"] != "oraculo", r["perfil"]))
        lines += ["", "Por cenário (✅ passou · ❌ falhou):", "",
                  "| Cenário | " + " | ".join(r["perfil"] for r in profiles) + " |",
                  "|---|" + "---|" * len(profiles)]  # fmt: skip
        for cid in ids:
            cells = []
            for r in profiles:
                c = next((x for x in r["cenarios_detalhe"] if x["id"] == cid), None)
                cells.append("—" if c is None else "✅" if c["passou"] else "❌")
            lines.append(f"| `{cid}` | " + " | ".join(cells) + " |")
        lines.append("")
        for r in profiles:
            fails = [
                (c["id"], t["user"], t["failures"])
                for c in r["cenarios_detalhe"]
                for t in c["turnos"]
                if t["failures"]
            ]
            if fails:
                lines += [f"Falhas de `{r['perfil']}`:", ""]
                lines += [f'- `{cid}` · "{u}": {"; ".join(f)}' for cid, u, f in fails]
                lines.append("")
            if notes := r.get("notas"):
                lines += [f"Notas de `{r['perfil']}`:", "", *(f"- {n}" for n in notes), ""]
    if cls := _load(out_dir, "classificador"):
        c = cls[-1]
        lines += [
            "## Classificador de intenção e guardrails (evals/intents.yaml)",
            "",
            (
                f"Acerto exato: **{_pct(c['acerto_exato'])}** de {c['total']} frases "
                f"({c['ms_por_frase']} ms por frase)."
            ),
            "",
            "| Rótulo | Precisão | Revocação | F1 | Suporte |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {k} | {_pct(v['precisao'])} | {_pct(v['revocacao'])} | {v['f1']:.2f} "
            f"| {v['suporte']} |"
            for k, v in c["rotulos"].items()
        ]
        if c["erros"]:
            lines += ["", "Erros:", ""]
            lines += [
                f'- "{e["frase"]}": esperado {e["esperado"]}, obtido {e["obtido"]}'
                for e in c["erros"]
            ]
        lines.append("")
    if ret := _load(out_dir, "busca"):
        r = ret[-1]
        lines += [
            "## Busca: lexical × híbrida (evals/retrieval.yaml)",
            "",
            "| Busca | hit@1 | hit@3 | MRR | p50 por consulta |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {b['busca']} | {_pct(b['hit@1'])} | {_pct(b['hit@3'])} | {b['mrr']:.3f} "
            f"| {b['ms_p50']} ms |"
            for b in r["resultados"]
        ]
        lines.append("")
    path = out_dir / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
