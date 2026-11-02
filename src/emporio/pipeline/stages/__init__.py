"""Registro das etapas do pipeline do projeto.

Para adicionar uma etapa: escreva a função `f(ctx: StageContext) -> dict | None` num módulo
deste pacote e declare-a em `build_stages`, com entradas, saídas e dependências.
"""

from __future__ import annotations

from emporio.config import Settings
from emporio.pipeline.core import Pipeline, Stage
from emporio.pipeline.stages.raw import snapshot_raw


def build_stages(settings: Settings) -> list[Stage]:
    processed = settings.processed_dir
    return [
        Stage(
            name="snapshot_raw",
            func=snapshot_raw,
            inputs=(settings.raw_dir,),
            outputs=(processed / "raw_snapshot.json",),
            description="inventário das fontes brutas",
        ),
    ]


def build_pipeline(settings: Settings) -> Pipeline:
    return Pipeline(
        build_stages(settings),
        state_file=settings.pipeline_state_file,
        root=settings.project_root,
        settings=settings,
    )
