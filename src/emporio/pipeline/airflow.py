"""Ponte para orquestradores (Airflow, Dagster): o DAG sai das mesmas etapas do pipeline.

- `task_graph(settings)`: uma task por etapa, com as mesmas dependências de `build_stages`. O DAG
  em `dags/emporio_dag.py` é gerado a partir daqui, então nunca diverge do pipeline local.
- `run_task(stage)`: o callable de cada task. Executa só aquela etapa (`Pipeline.run_stage`), com o
  mesmo cache por conteúdo e a mesma linhagem em `data/processed/manifest.json`; falha (exceção)
  se uma dependência não estiver em dia ou se a etapa falhar, para o orquestrador parar ali.

Não depende do Airflow: o pacote só é importado pelo arquivo do DAG, dentro do Airflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from emporio.config import Settings
from emporio.pipeline.stages import build_pipeline


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    upstream: tuple[str, ...]
    doc: str


def task_graph(settings: Settings | None = None) -> list[TaskSpec]:
    """Tasks em ordem topológica, cada uma com as etapas de que depende."""
    pipe = build_pipeline(settings or Settings())
    return [
        TaskSpec(name, tuple(sorted(pipe.stages[name].deps)), pipe.stages[name].description)
        for name in pipe.order()
    ]


def run_task(stage: str, settings: Settings | None = None) -> dict[str, Any]:
    """Executa uma etapa e devolve o resultado (vai para o XCom no Airflow)."""
    result = build_pipeline(settings or Settings()).run_stage(stage)
    return {"stage": result.name, "status": result.status, "duration_s": result.duration_s,
            "metrics": result.metrics}  # fmt: skip
