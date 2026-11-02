"""Registro das etapas do pipeline do projeto.

Para adicionar uma etapa: escreva a função `f(ctx: StageContext) -> dict | None` num módulo
deste pacote e declare-a em `build_stages`, com entradas, saídas, dependências e os módulos
de código que, se mudarem, devem invalidá-la (`code`).
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from emporio.config import Settings
from emporio.data import rules, schemas
from emporio.pipeline.core import Pipeline, Stage
from emporio.pipeline.stages import tables
from emporio.pipeline.stages.raw import snapshot_raw


def build_stages(settings: Settings) -> list[Stage]:
    processed = settings.processed_dir
    csvs = tables.raw_paths(settings.raw_dir)
    schema_sql = Path(str(files("emporio.data").joinpath("schema.sql")))
    return [
        Stage(
            name="snapshot_raw",
            func=snapshot_raw,
            inputs=(settings.raw_dir,),
            outputs=(processed / "raw_snapshot.json",),
            description="inventário das fontes brutas",
        ),
        Stage(
            name="validate_raw",
            func=tables.validate_raw,
            deps=("snapshot_raw",),
            inputs=csvs,
            outputs=(processed / "validation_report.json",),
            code=(schemas,),
            description="contratos dos CSVs + integridade",
        ),
        Stage(
            name="transform",
            func=tables.transform,
            deps=("validate_raw",),
            inputs=csvs,
            outputs=(processed / "staging",),
            code=(schemas, rules),
            description="decisões do relatório",
        ),
        Stage(
            name="load",
            func=tables.load,
            deps=("transform",),
            inputs=(processed / "staging", schema_sql),
            outputs=(settings.store_db,),
            description="SQLite com CHECKs",
        ),
        Stage(
            name="quality_checks",
            func=tables.quality_checks,
            deps=("load",),
            inputs=(settings.store_db,),
            outputs=(processed / "quality_report.json",),
            description="invariantes no banco",
        ),
    ]


def build_pipeline(settings: Settings) -> Pipeline:
    return Pipeline(
        build_stages(settings),
        state_file=settings.pipeline_state_file,
        root=settings.project_root,
        settings=settings,
    )
