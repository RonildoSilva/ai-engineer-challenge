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
emporio order show 8 --contato email|telefone              pedido (verificação LGPD)
emporio order return 7 --contato … --motivo "me arrependi" troca/devolução com prazos
emporio rules pay 599.90 [--promo] [--parcelas 10]         formas de pagamento e parcelamento
emporio rules ship 349.90 --cidade "Campo Grande"          frete
emporio rules hours [--at 2026-11-02T10:00]                loja aberta/fechada agora
emporio mcp [--http] [--host 127.0.0.1] [--port 8000]      servidor MCP (stdio ou Streamable HTTP)
emporio llm list                                           perfis de LLM configurados
emporio llm check [--profile bonsai]                       conexão, contexto, chat e tool calling
emporio ask "Quanto custa o Takamine GD20?" [--debug]      uma pergunta ao agente, de ponta a ponta
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from datetime import datetime

from pydantic import ValidationError

from emporio.adapters.sqlite_repo import SqliteRepository
from emporio.agent.agent import Agent
from emporio.agent.budget import ContextBudget
from emporio.agent.llm import (
    LLMClient,
    LLMConfigError,
    LLMUnavailableError,
    get_profile,
    load_profiles,
)
from emporio.agent.mcp_client import McpToolbox
from emporio.config import Settings
from emporio.data import policies as pol
from emporio.domain.calculators import check_installments, payment_plan, shipping_quote
from emporio.domain.catalog import CatalogService, ProductView
from emporio.domain.orders import OrderService
from emporio.domain.policies import PolicyStore
from emporio.domain.store_hours import store_status, weekly_hours
from emporio.log import configure_logging, get_logger
from emporio.mcp_server.server import build_server
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

    _add_service_commands(sub)
    return p


def _add_service_commands(sub: argparse._SubParsersAction) -> None:
    """Comandos de pedidos, regras, MCP e LLM."""
    order = sub.add_parser("order", help="pedidos (exige e-mail ou telefone do titular)")
    order_sub = order.add_subparsers(dest="action", required=True)
    oshow = order_sub.add_parser("show", help="consulta um pedido")
    oret = order_sub.add_parser("return", help="avalia troca/devolução")
    for sp in (oshow, oret):
        sp.add_argument("order")
        sp.add_argument("--contato", required=True, help="e-mail ou telefone do titular")
    oret.add_argument("--motivo", default="", help="ex.: 'me arrependi', 'veio com defeito'")

    rules = sub.add_parser("rules", help="regras da loja").add_subparsers(
        dest="action", required=True
    )
    pay = rules.add_parser("pay", help="formas de pagamento para um valor")
    pay.add_argument("valor", type=float)
    pay.add_argument("--promo", action="store_true", help="o preço já é promocional")
    pay.add_argument("--parcelas", type=int, help="verifica um número de parcelas")
    ship = rules.add_parser("ship", help="frete para um valor e cidade")
    ship.add_argument("valor", type=float)
    ship.add_argument("--cidade", required=True)
    ship.add_argument("--categoria", type=int)
    hours = rules.add_parser("hours", help="loja aberta ou fechada")
    hours.add_argument("--at", help="data/hora ISO (padrão: agora, em Campo Grande)")

    mcp = sub.add_parser("mcp", help="servidor MCP (stdio por padrão)")
    mcp.add_argument("--http", action="store_true", help="Streamable HTTP em /mcp")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8000)

    llm = sub.add_parser("llm", help="perfis de LLM").add_subparsers(dest="action", required=True)
    llm.add_parser("list", help="perfis configurados")
    check = llm.add_parser("check", help="testa um perfil (sobe o servidor local se preciso)")
    check.add_argument("--profile", help="nome do perfil (padrão: LLM_PROFILE ou o default)")

    ask = sub.add_parser("ask", help="uma pergunta ao agente (sobe os servidores necessários)")
    ask.add_argument("question")
    ask.add_argument("--profile", help="perfil de LLM (padrão: LLM_PROFILE ou o default)")
    ask.add_argument("--debug", action="store_true", help="mostra as tools chamadas")


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


def _order_cmd(args: argparse.Namespace, settings: Settings) -> int:
    svc = OrderService(SqliteRepository(settings.store_db), settings.reference_date)
    if args.action == "show":
        r = svc.lookup(args.order, args.contato)
        print(r.view.describe() if r.view else r.message)
        return 0 if r.verified else 1
    a = svc.assess_return(args.order, args.contato, args.motivo)
    if not a.verified:
        print(a.message)
        return 1
    status = "elegível" if a.eligible else "não elegível"
    print(f"[{a.reason or 'motivo?'} · {status}] {a.message}")
    for line in [a.rule, *a.conditions, *a.next_steps]:
        if line:
            print(f"  - {line}")
    return 0


def _rules_cmd(args: argparse.Namespace, settings: Settings) -> int:  # noqa: ARG001
    if args.action == "pay":
        print(payment_plan(args.valor, has_promotion=args.promo).describe())
        if args.parcelas:
            print(check_installments(args.valor, args.parcelas)[1])
    elif args.action == "ship":
        print(shipping_quote(args.valor, args.cidade, category_id=args.categoria).describe())
    else:
        when = datetime.fromisoformat(args.at) if args.at else None
        print(store_status(when).describe())
        print(weekly_hours())
    return 0


def _mcp_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """Servidor MCP. O servidor de embeddings sobe junto e é encerrado ao sair."""

    def run(url: str | None) -> int:
        server = build_server(settings, embedding_url=url)
        if args.http:
            import uvicorn  # noqa: PLC0415 — só o transporte HTTP precisa do servidor ASGI

            log.info(
                "MCP via Streamable HTTP", extra={"url": f"http://{args.host}:{args.port}/mcp"}
            )
            uvicorn.run(
                server.streamable_http_app(), host=args.host, port=args.port, log_level="warning"
            )
        else:
            server.run("stdio")  # stdout é do protocolo: logs vão para stderr
        return 0

    return _with_embeddings(settings, run)


def _exit_on_signal(signum: int, _frame: object) -> None:
    """SIGTERM/SIGHUP viram `SystemExit`: blocos `finally` rodam e servidores filhos são encerrados.

    Sem isso, o processo morre na hora (o uvicorn, por exemplo, re-dispara o SIGTERM depois do
    desligamento gracioso) e o servidor de embeddings iniciado por `ensure_server` fica órfão.
    """
    raise SystemExit(128 + signum)


PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "detalhar_produto",
        "description": "Preço, promoção e disponibilidade de um modelo de instrumento.",
        "parameters": {
            "type": "object",
            "properties": {"produto": {"type": "string"}},
            "required": ["produto"],
        },
    },
}


def _llm_cmd(args: argparse.Namespace, settings: Settings) -> int:
    if args.action == "list":
        data = load_profiles(settings.path(settings.llm_profiles_file))
        active = settings.llm_profile or data.default
        for name, p in data.profiles.items():
            mark = "*" if name == active else " "
            tested = "testado" if p.tested else "referência"
            local = " [local]" if p.local else ""
            print(f"{mark} {name:<20} {p.model:<18} ctx={p.context_window!s:<7} {tested}{local}")
            print(f"    {p.description}")
        return 0

    profile = get_profile(settings, args.profile)

    def run(url: str) -> int:
        client = LLMClient(profile.model_copy(update={"base_url": url}))
        print(f"perfil: {profile.name} · modelo: {profile.resolved_model()} · {url}")
        print(f"contexto: {client.context_window()} tokens")
        hello = client.chat([{"role": "user", "content": "Responda só: olá"}], max_tokens=16)
        print(f"chat: {hello.content[:60]!r} em {hello.latency_s:.1f} s")
        probe = client.chat(
            [{"role": "system", "content": "Atendente de loja de instrumentos. Use ferramentas."},
             {"role": "user", "content": "Quanto custa o Takamine GD20?"}],
            tools=[PROBE_TOOL], temperature=0,
        )  # fmt: skip
        calls = [(c.name, c.arguments) for c in probe.tool_calls]
        print(f"tool calling: {calls or 'nenhuma chamada'} em {probe.latency_s:.1f} s")
        return 0 if calls else 1

    try:
        if profile.local and not LLMClient(profile).ping():
            with ensure_server(
                profile.local.mode,
                profile.base_url,
                settings.path(profile.local.model_file),
                log_dir=settings.processed_dir / "logs",
            ) as url:
                return run(url)
        return run(profile.base_url)
    except (LLMUnavailableError, ServerUnavailableError) as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1


def _ask_cmd(args: argparse.Namespace, settings: Settings) -> int:
    profile = get_profile(settings, args.profile)

    def run(embedding_url: str | None) -> int:
        with contextlib.ExitStack() as stack:
            llm_url = profile.base_url
            if profile.local and not LLMClient(profile).ping():
                llm_url = stack.enter_context(
                    ensure_server(
                        profile.local.mode,
                        profile.base_url,
                        settings.path(profile.local.model_file),
                        log_dir=settings.processed_dir / "logs",
                    )
                )
            llm = LLMClient(profile.model_copy(update={"base_url": llm_url}))
            server = build_server(settings, embedding_url=embedding_url)

            async def go():
                async with McpToolbox(server) as toolbox:
                    budget = ContextBudget(llm.context_window(), profile.max_output_tokens)
                    agent = Agent(
                        llm, toolbox, reference_date=settings.reference_date, budget=budget
                    )
                    return await agent.respond(args.question)

            turn = asyncio.run(go())
        print(turn.answer)
        if args.debug:
            print(
                f"\n— {turn.llm_calls} chamada(s) ao LLM em {turn.latency_s:.1f} s, "
                f"prompt ~{turn.prompt_tokens} tokens",
                file=sys.stderr,
            )
            if turn.route:
                intents = ", ".join(i.value for i in turn.route.intents)
                offered = ", ".join(turn.tools_offered) or "nenhuma"
                print(f"  · assunto: {intents} | tools oferecidas: {offered}", file=sys.stderr)
            for t in turn.prefetched:
                print(f"  · pré-busca {t.name}({t.arguments}) {t.output[:160]}", file=sys.stderr)
            if turn.price_retry:
                print(f"  · valores sem fonte corrigidos: {turn.price_retry}", file=sys.stderr)
            for t in turn.tools:
                status = "ok" if t.ok else "erro"
                print(f"  · {t.name}({t.arguments}) [{status}] {t.output[:160]}", file=sys.stderr)
            if turn.fallback:
                print(f"  · resposta de segurança: {turn.fallback}", file=sys.stderr)
        return 0 if not turn.fallback else 1

    try:
        return _with_embeddings(settings, run)
    except ServerUnavailableError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    for sig in (signal.SIGTERM, signal.SIGHUP):  # kill e terminal fechado
        signal.signal(sig, _exit_on_signal)
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
        "order": _order_cmd,
        "rules": _rules_cmd,
        "mcp": _mcp_cmd,
        "llm": _llm_cmd,
        "ask": _ask_cmd,
    }
    try:
        return commands[args.command](args, settings)
    except (PipelineError, pol.PolicyError, LLMConfigError, FileNotFoundError) as e:
        print(f"✘ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
