"""CLI do projeto: `emporio <comando>`.

emporio pipeline run [--stage NOME ...] [--force]   executa o pipeline (incremental)
emporio pipeline status                            estado de cada etapa
emporio pipeline graph                             DAG em Mermaid
emporio policies list                              seções do manual publicadas
emporio policies search "pergunta" [-k N]          seções mais relevantes para a pergunta
emporio policies lock                              registra a versão do PDF após revisar a curadoria
emporio search "pergunta" [--kind product|policy] [-k N]   busca híbrida (lexical + embeddings)
emporio catalog search "violões até R$1000" [--all]        produtos com preço/PIX/promoção
emporio catalog show "takamine gd20"                       detalhe, ambiguidade e alternativas
"""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError

from emporio.adapters.sqlite_repo import SqliteRepository
from emporio.config import Settings
from emporio.data import policies as pol
from emporio.domain.catalog import CatalogService, ProductView
from emporio.domain.policies import PolicyStore
from emporio.log import configure_logging, get_logger
from emporio.pipeline.core import PipelineError
from emporio.pipeline.stages import build_pipeline
from emporio.retrieval.factory import build_retriever
from emporio.services import ServerUnavailableError, ensure_server

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

    search_all = sub.add_parser("search", help="busca híbrida em produtos e políticas")
    search_all.add_argument("query")
    search_all.add_argument("--kind", choices=["product", "policy"])
    search_all.add_argument("-k", type=int, default=5, help="máximo de resultados")

    catalog = sub.add_parser("catalog", help="catálogo de produtos").add_subparsers(
        dest="action", required=True
    )
    csearch = catalog.add_parser("search", help="busca com filtros inferidos do texto")
    csearch.add_argument("query")
    csearch.add_argument("--all", action="store_true", help="inclui indisponíveis")
    csearch.add_argument("-k", type=int, default=8)
    cshow = catalog.add_parser("show", help="detalhe de um produto (nome aproximado ou id)")
    cshow.add_argument("ref")
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


def _search_cmd(args: argparse.Namespace, settings: Settings) -> int:
    def run_search(embedding_url: str | None = None) -> int:
        retriever = build_retriever(settings, embedding_url)
        hits = retriever.search(args.query, k=args.k, kind=args.kind)
        reason = getattr(retriever, "degraded_reason", None)
        mode = type(retriever).__name__ + (f" (degradado: {reason})" if reason else "")
        print(f"[{mode}]")
        if not hits:
            print("(nenhum resultado relevante)")
        for i, h in enumerate(hits, start=1):
            ranks = " ".join(f"{k}#{v}" for k, v in h.ranks.items())
            print(f"{i:>2}. {h.doc_id:<20} {h.kind:<8} {h.score:8.4f}  [{ranks}]  {h.title}")
        return 0

    if settings.embedding_backend != "llamafile":
        return run_search()
    try:
        with ensure_server(
            "embedding",
            settings.embedding_base_url,
            settings.path(settings.embedding_model_file),
            log_dir=settings.processed_dir / "logs",
        ) as url:
            return run_search(url)
    except ServerUnavailableError as e:
        print(f"⚠ {e} — usando só a busca lexical", file=sys.stderr)
        return run_search()


def _with_embeddings(settings: Settings, fn):
    """Executa `fn(embedding_url)` com o servidor de embeddings disponível (ou degradado)."""
    if settings.embedding_backend != "llamafile":
        return fn(None)
    try:
        with ensure_server(
            "embedding",
            settings.embedding_base_url,
            settings.path(settings.embedding_model_file),
            log_dir=settings.processed_dir / "logs",
        ) as url:
            return fn(url)
    except ServerUnavailableError as e:
        print(f"⚠ {e} — usando só a busca lexical", file=sys.stderr)
        return fn(None)


def _fmt_view(v: ProductView) -> str:
    p = v.product
    flag = "" if p.is_sellable else f" [{p.availability}]"
    return f"{p.product_id:>4}  {p.name}{flag} — {v.price.describe()}"


def _catalog_cmd(args: argparse.Namespace, settings: Settings) -> int:
    def run(url: str | None) -> int:
        svc = CatalogService(SqliteRepository(settings.store_db), build_retriever(settings, url))
        if args.action == "search":
            r = svc.search(args.query, only_available=not args.all, limit=args.k)
            filters = {k: v for k, v in r.filters.items() if v not in (None, False)}
            print(f"[{r.scope}] filtros: {filters}")
            for v in r.items:
                print(_fmt_view(v))
            if r.note:
                print(f"nota: {r.note}")
            return 0
        d = svc.detail(args.ref)
        print(f"[{d.status}]")
        if d.product:
            print(_fmt_view(d.product))
        for c in d.candidates:
            print("  candidato:", _fmt_view(c))
        for a in d.alternatives:
            print("  alternativa:", _fmt_view(a))
        if d.note:
            print(f"nota: {d.note}")
        return 0

    return _with_embeddings(settings, run)


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
    commands = {
        "pipeline": _pipeline_cmd,
        "policies": _policies_cmd,
        "search": _search_cmd,
        "catalog": _catalog_cmd,
    }
    try:
        return commands[args.command](args, settings)
    except (PipelineError, pol.PolicyError, FileNotFoundError) as e:
        print(f"✘ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
