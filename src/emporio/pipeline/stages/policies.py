"""Etapas do manual de políticas: extract_policies → build_policies.

- extract_policies: PDF → seções numeradas (texto + hash), para revisão e verificação.
- build_policies:   valida a curadoria contra a extração (cobertura, versão do PDF, fatos) e
                    publica `policies.json`, consumido pelo domínio e pelo servidor MCP.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from emporio.data import policies as pol
from emporio.pipeline.core import StageContext


def extract_policies(ctx: StageContext) -> dict[str, Any]:
    pdf = ctx.inputs[0]
    sections = pol.extract_sections(pdf)
    out_json, out_md = ctx.outputs
    out_json.write_text(
        json.dumps(
            [{**asdict(s), "digest": s.digest} for s in sections], indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    # versão legível, útil para revisar a curadoria lado a lado com o PDF
    out_md.write_text(
        "\n\n".join(f"<!-- seção {s.ref} -->\n{s.text}" for s in sections), encoding="utf-8"
    )
    return {"sections": len(sections), "chars": sum(len(s.text) for s in sections)}


def build_policies(ctx: StageContext) -> dict[str, Any]:
    extracted, curated_dir = ctx.inputs
    source = [
        pol.SourceSection(s["ref"], s["title"], s["text"])
        for s in json.loads(extracted.read_text(encoding="utf-8"))
    ]
    curated = pol.load_curated(curated_dir)
    pol.check(source, curated, pol.read_lock(curated_dir))

    payload = [
        {
            "id": c.id,
            "title": c.title,
            "pdf_refs": list(c.pdf_refs),
            "kind": c.kind,
            "keywords": list(c.keywords),
            "tokens_est": c.tokens_est,
            "body": c.body,
        }
        for c in curated
    ]
    ctx.outputs[0].write_text(json.dumps(payload, indent=2, ensure_ascii=False), "utf-8")
    return {
        "sections": len(payload),
        "tokens_est_total": sum(p["tokens_est"] for p in payload),
        "guidelines": [p["id"] for p in payload if p["kind"] == "guideline"],
    }
