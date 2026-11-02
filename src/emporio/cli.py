"""CLI do projeto: `emporio <comando>`.

emporio pipeline run [--stage NOME ...] [--force]   executa o pipeline (incremental)
emporio pipeline status                            estado de cada etapa
emporio pipeline graph                             DAG em Mermaid
"""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError

from emporio.config import Settings
from emporio.log import configure_logging, get_logger
from emporio.pipeline.core import PipelineError
from emporio.pipeline.stages import build_pipeline

log = get_logger(__name__)

ICONS = {
    "ok": "✔",
    "up_to_date": "≡",
    "failed": "✘",
    "skipped": "↷",
    "stale": "…",
    "never_run": "○",
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="emporio", description="Agente da Empório da Música")
    sub = p.add_subparsers(dest="command", required=True)

    pipe = sub.add_parser("pipeline", help="pipeline de dados").add_subparsers(
        dest="action", required=True
    )
    run = pipe.add_parser("run", help="executa as etapas (pula o que está em dia)")
    run.add_argument(
        "--stage", action="append", help="etapa alvo (repetível); inclui as anteriores"
    )
    run.add_argument("--force", action="store_true", help="reexecuta mesmo se estiver em dia")
    pipe.add_parser("status", help="estado de cada etapa")
    pipe.add_parser("graph", help="imprime o DAG em Mermaid")
    return p


def _pipeline_cmd(args: argparse.Namespace, settings: Settings) -> int:
    pipeline = build_pipeline(settings)
    if args.action == "graph":
        print(pipeline.mermaid())
        return 0
    if args.action == "status":
        for name, st in pipeline.status().items():
            print(f"{ICONS.get(st, '?')} {name:<24} {st}")
        return 0

    report = pipeline.run(targets=args.stage, force=args.force)
    for r in report.results:
        extra = f"  {r.metrics}" if r.metrics else ""
        err = f"  → {r.error}" if r.error else ""
        icon = ICONS.get(r.status, "?")
        print(f"{icon} {r.name:<24} {r.status:<11} {r.duration_s:6.2f}s{extra}{err}")
    print(f"\nlinhagem: {pipeline.state_file}")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as e:
        print("✘ configuração inválida (.env / variáveis de ambiente):", file=sys.stderr)
        for err in e.errors():
            print(f"  - {'.'.join(map(str, err['loc']))}: {err['msg']}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level, settings.log_format)
    try:
        if args.command == "pipeline":
            return _pipeline_cmd(args, settings)
    except PipelineError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
