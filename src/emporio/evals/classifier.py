"""Avaliação isolada do classificador: detecção de assunto + guardrails de entrada.

Multirrótulo: cada frase tem um conjunto de intenções esperado (ou um bloqueio). Métricas:
acerto exato (o conjunto inteiro), e precisão/revocação/F1 por rótulo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from emporio.agent.guardrails import check_input
from emporio.agent.router import Intent, detect_intents


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    intents: list[str] = []
    guardrail: str | None = None

    @model_validator(mode="after")
    def _one_label(self) -> Item:
        if bool(self.intents) == bool(self.guardrail):
            raise ValueError(f"'{self.text}': informe `intents` ou `guardrail` (um dos dois)")
        valid = {i.value for i in Intent}
        if bad := set(self.intents) - valid:
            raise ValueError(f"'{self.text}': intenções desconhecidas {sorted(bad)}")
        return self

    @property
    def expected(self) -> frozenset[str]:
        return frozenset([f"!{self.guardrail}"] if self.guardrail else self.intents)


def load_items(path: Path) -> list[Item]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Item.model_validate(r) for r in raw["items"]]


def predict(text: str) -> frozenset[str]:
    """O que o agente faria com a frase: bloqueio (prefixo "!") ou as intenções detectadas."""
    intents = detect_intents(text)
    if v := check_input(text, store_subject=bool(intents)):
        return frozenset([f"!{v.block.value}"])
    return frozenset(i.value for i in intents) or frozenset([Intent.GERAL.value])


@dataclass
class LabelScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class ClassifierReport:
    total: int
    exact: int
    labels: dict[str, LabelScore]
    errors: list[dict[str, object]] = field(default_factory=list)
    ms_per_item: float = 0.0

    @property
    def accuracy(self) -> float:
        return self.exact / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "acerto_exato": round(self.accuracy, 3),
            "ms_por_frase": round(self.ms_per_item, 3),
            "rotulos": {
                k: {
                    "precisao": round(v.precision, 3),
                    "revocacao": round(v.recall, 3),
                    "f1": round(v.f1, 3),
                    "suporte": v.tp + v.fn,
                }
                for k, v in sorted(self.labels.items())
            },
            "erros": self.errors,
        }


def evaluate(items: list[Item]) -> ClassifierReport:
    labels: dict[str, LabelScore] = {}
    errors: list[dict[str, object]] = []
    exact = 0
    t0 = time.perf_counter()
    for item in items:
        got, want = predict(item.text), item.expected
        if got == want:
            exact += 1
        else:
            errors.append({"frase": item.text, "esperado": sorted(want), "obtido": sorted(got)})
        for label in got | want:
            score = labels.setdefault(label, LabelScore())
            if label in got and label in want:
                score.tp += 1
            elif label in got:
                score.fp += 1
            else:
                score.fn += 1
    elapsed = (time.perf_counter() - t0) * 1000 / max(1, len(items))
    return ClassifierReport(len(items), exact, labels, errors, elapsed)
