"""CLI do projeto: `emporio <comando>`.

emporio pipeline run [--stage NOME ...] [--force]   executa o pipeline (incremental)
emporio pipeline status                            estado de cada etapa
emporio pipeline graph                             DAG em Mermaid
emporio policies list                              seções do manual publicadas
emporio policies search "pergunta" [-k N]          seções mais relevantes para a pergunta
emporio policies lock                              registra a versão do PDF após revisar a curadoria
"""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError

from emporio.config import Settings
from emporio.data import policies as pol
from emporio.domain.policies import PolicyStore
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

    policies = sub.add_parser("policies", help="manual de políticas").add_subparsers(
        dest="action", required=True
    )
    policies.add_parser("list", help="seções publicadas")
    search = policies.add_parser("search", help="seções mais relevantes para uma pergunta")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=3, help="máximo de seções")
    policies.add_parser("lock", help="registra o hash das seções do PDF após revisar a curadoria")
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


def _policies_cmd(args: argparse.Namespace, settings: Settings) -> int:
    if args.action == "lock":
        pdf = settings.raw_dir / "politicas_da_loja.pdf"
        lock = pol.make_lock(pdf, pol.extract_sections(pdf))
        path = pol.write_lock(settings.policies_curated_dir, lock)
        print(f"✔ versão do PDF registrada em {path}")
        return 0

    store = PolicyStore.from_json(settings.policies_json)
    if args.action == "list":
        for s in store.index():
            refs = ",".join(s.pdf_refs)
            print(f"{s.id:<20} {s.kind:<9} ~{s.tokens_est:>4} tokens  §{refs:<7} {s.title}")
        return 0

    hits = store.search(args.query, k=args.k)
    if not hits:
        print("(nenhuma seção relevante)")
    for h in hits:
        print(f"{h.score:5.1f}  {h.section.id:<20} {', '.join(h.matched)}")
    return 0


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
        if args.command == "policies":
            return _policies_cmd(args, settings)
    except (PipelineError, pol.PolicyError, FileNotFoundError) as e:
        print(f"✘ {e}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
