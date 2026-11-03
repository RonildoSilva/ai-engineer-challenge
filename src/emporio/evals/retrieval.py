"""Avaliação da busca com os retrievers de produção: lexical × híbrida (lexical + vetores, RRF).

Gabarito em evals/retrieval.yaml (o mesmo da escolha do modelo de embeddings, commit 6).
Métricas: hit@1, hit@3, MRR (posição do primeiro documento aceito) e latência por consulta.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from emporio.ports import Retriever


@dataclass(frozen=True)
class Query:
    text: str
    accept: frozenset[str]


def load_queries(path: Path) -> list[Query]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Query(q["query"], frozenset(q["accept"])) for q in raw["queries"]]


@dataclass
class RetrievalReport:
    name: str
    hit1: float
    hit3: float
    mrr: float
    ms_p50: float
    misses: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "busca": self.name,
            "hit@1": round(self.hit1, 3),
            "hit@3": round(self.hit3, 3),
            "mrr": round(self.mrr, 3),
            "ms_p50": round(self.ms_p50, 1),
            "erros_no_top1": self.misses,
        }


def evaluate(name: str, retriever: Retriever, queries: list[Query], k: int = 10) -> RetrievalReport:
    ranks: list[int | None] = []
    times: list[float] = []
    misses: list[dict[str, object]] = []
    for q in queries:
        t0 = time.perf_counter()
        hits = retriever.search(q.text, k=k)
        times.append((time.perf_counter() - t0) * 1000)
        ids = [h.doc_id for h in hits]
        rank = next((i for i, d in enumerate(ids, 1) if d in q.accept), None)
        ranks.append(rank)
        if rank != 1:
            misses.append({"pergunta": q.text, "top3": ids[:3], "posicao_certa": rank})
    n = len(queries)
    return RetrievalReport(
        name,
        hit1=sum(r == 1 for r in ranks) / n,
        hit3=sum(r is not None and r <= 3 for r in ranks) / n,  # noqa: PLR2004
        mrr=sum(1 / r for r in ranks if r) / n,
        ms_p50=statistics.median(times),
        misses=misses,
    )
