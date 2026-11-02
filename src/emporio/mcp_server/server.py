"""Servidor MCP: camada fina sobre o domínio (catálogo, pedidos, regras, políticas).

- Tools somente leitura, com docstrings em PT-BR (é o texto que o LLM lê para escolher).
- Saída estruturada (modelos de `schemas.py`) com um campo `resposta` já redigido pelas regras.
- Resources: manual de políticas por seção (com custo estimado em tokens) e categorias.
- Prompt `persona_atendente`: a persona "Mel" e as regras do atendimento.
- Erros esperados viram `ToolError` com mensagem amigável; erros inesperados são registrados no log
  e chegam ao cliente sem detalhes internos (comportamento padrão do SDK).
- Nenhuma tool lista clientes. Logs nunca registram e-mail ou telefone.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from functools import wraps

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations

from emporio.adapters.sqlite_repo import SqliteRepository
from emporio.config import Settings
from emporio.domain.calculators import check_installments, payment_plan, shipping_quote
from emporio.domain.catalog import CatalogService, ProductView, detect_category
from emporio.domain.orders import OrderService
from emporio.domain.policies import PolicyStore
from emporio.domain.store_hours import SPECIAL_HOURS_NOTE, store_status, weekly_hours
from emporio.log import get_logger
from emporio.mcp_server import schemas as out
from emporio.mcp_server.persona import persona_prompt
from emporio.retrieval.factory import build_retriever

log = get_logger(__name__)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
PII_ARGS = {"email_ou_telefone"}

INSTRUCTIONS = """Servidor da Empório da Música (instrumentos musicais, Campo Grande/MS).
Use buscar_produtos para opções e preços por categoria/faixa; detalhar_produto para um modelo
específico; consultar_politica para regras (trocas, pagamento, frete, horário, garantia,
privacidade);
consultar_pedido e verificar_devolucao exigem número do pedido + e-mail ou telefone do titular;
simular_pagamento, calcular_frete e status_loja fazem as contas. Todas são somente leitura."""


def _logged[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Log estruturado por chamada (nome, duração, resultado), sem dados pessoais."""

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        safe = {k: ("***" if k in PII_ARGS else v) for k, v in kwargs.items()}
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except ToolError as e:
            log.info("tool recusada", extra={"tool": fn.__name__, "params": safe, "reason": str(e)})
            raise
        except Exception:
            log.exception("erro inesperado na tool", extra={"tool": fn.__name__, "params": safe})
            raise
        log.info(
            "tool executada",
            extra={
                "tool": fn.__name__,
                "params": safe,
                "ms": round((time.perf_counter() - t0) * 1000),
            },
        )
        return result

    return wrapper


def _product(v: ProductView) -> out.Produto:
    p, price = v.product, v.price
    promo = f"{price.promotion.label} ({price.promotion.percent}%)" if price.promotion else None
    return out.Produto(
        id=p.product_id,
        nome=p.name,
        categoria=p.category_name,
        disponibilidade=p.availability,
        preco=price.list_price,
        preco_final=price.final_price,
        promocao=promo,
        preco_pix=price.pix_price,
        resumo_preco=price.describe(),
        especificacoes=p.specs,
        descricao=p.description,
    )


def build_server(
    settings: Settings,
    *,
    embedding_url: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> MCPServer:
    """Monta o servidor com os serviços de domínio. `now` permite fixar o relógio em testes."""
    repo = SqliteRepository(settings.store_db)
    retriever = build_retriever(settings, embedding_url)
    catalog = CatalogService(repo, retriever)
    orders = OrderService(repo, settings.reference_date)
    policies = PolicyStore.from_json(settings.policies_json)

    mcp = MCPServer(
        "emporio-da-musica",
        title="Empório da Música",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    # ------------------------------------------------------------------ catálogo

    @mcp.tool(annotations=READ_ONLY)
    @_logged
    def buscar_produtos(
        texto: str = "",
        categoria: str | None = None,
        preco_min: float | None = None,
        preco_max: float | None = None,
        apenas_disponiveis: bool = True,
        limite: int = 5,
    ) -> out.BuscaProdutos:
        """Busca instrumentos no catálogo com preço, promoção, PIX e disponibilidade.

        Use para perguntas como "violões até R$1000", "ukulele para criança", "guitarra para metal".
        Categoria e faixa de preço são inferidas do texto quando não informadas.
        """
        r = catalog.search(
            texto,
            category=categoria,
            min_price=preco_min,
            max_price=preco_max,
            only_available=apenas_disponiveis,
            limit=max(1, min(limite, 10)),
        )
        return out.BuscaProdutos(
            situacao=r.scope,
            filtros={
                k: v for k, v in r.filters.items() if isinstance(v, str | float | bool) or v is None
            },
            produtos=[_product(v) for v in r.items],
            orientacao=r.note,
        )

    @mcp.tool(annotations=READ_ONLY)
    @_logged
    def detalhar_produto(produto: str) -> out.DetalheProduto:
        """Preço, promoção, PIX, disponibilidade e especificações de UM modelo.

        Aceita o nome aproximado ("takamine gd 20", "Quanto custa o Takamine GD20?") ou o id.
        Se o produto estiver esgotado ou descontinuado, traz alternativas disponíveis.
        """
        d = catalog.detail(produto)
        return out.DetalheProduto(
            situacao=d.status,
            produto=_product(d.product) if d.product else None,
            candidatos=[_product(c) for c in d.candidates],
            alternativas=[_product(a) for a in d.alternatives],
            orientacao=d.note,
        )

    # ------------------------------------------------------------------ pedidos

    @mcp.tool(annotations=READ_ONLY)
    @_logged
    def consultar_pedido(numero_pedido: str, email_ou_telefone: str) -> out.Pedido:
        """Status, itens, total, pagamento e rastreio de um pedido.

        Exige o número do pedido e o e-mail ou telefone do titular (LGPD). Peça esses dados ao
        cliente antes de chamar. Se não conferirem, a resposta não revela se o pedido existe.
        """
        r = orders.lookup(numero_pedido, email_ou_telefone)
        if not r.verified or r.view is None:
            return out.Pedido(verificado=False, resposta=r.message or "")
        o = r.view.order
        return out.Pedido(
            verificado=True,
            numero=o.order_id,
            cliente=r.view.first_name,
            status=r.view.status_label,
            data=o.order_date.isoformat(),
            itens=[out.ItemPedido(produto=i.product_name, quantidade=i.quantity) for i in o.items],
            total=o.total_brl,
            pagamento=r.view.payment_label,
            rastreio=o.tracking_code,
            previsao_entrega=o.estimated_delivery.isoformat() if o.estimated_delivery else None,
            atrasado=r.view.delayed,
            resposta=r.view.describe(),
        )

    @mcp.tool(annotations=READ_ONLY)
    @_logged
    def verificar_devolucao(
        numero_pedido: str, email_ou_telefone: str, motivo: str = ""
    ) -> out.Devolucao:
        """Avalia troca/devolução de um pedido pelas regras do manual (§4 e §8).

        `motivo`: palavras do cliente ("me arrependi", "veio com defeito", "quero outra cor") ou
        arrependimento | defeito | preferencia. Exige o número do pedido e o e-mail ou telefone
        do titular.
        """
        a = orders.assess_return(numero_pedido, email_ou_telefone, motivo)
        return out.Devolucao(
            verificado=a.verified,
            motivo=a.reason,
            elegivel=a.eligible,
            regra=a.rule or None,
            prazo_final=a.deadline.isoformat() if a.deadline else None,
            dias_restantes=a.days_left,
            condicoes=a.conditions,
            proximos_passos=a.next_steps,
            resposta=a.message,
        )

    # ------------------------------------------------------------------ regras

    @mcp.tool(annotations=READ_ONLY)
    @_logged
    def simular_pagamento(
        valor: float, preco_promocional: bool = False, parcelas: int | None = None
    ) -> out.Pagamento:
        """Formas de pagamento para um valor: PIX, débito, boleto e parcelamento sem juros.

        PIX tem 5% de desconto só sem promoção; o parcelamento respeita a parcela mínima por faixa.
        Use `parcelas` para responder "dá pra fazer em 10x?".
        """
        if valor <= 0:
            raise ToolError("Informe um valor positivo em reais.")
        plan = payment_plan(valor, has_promotion=preco_promocional)
        ok, msg = check_installments(valor, parcelas) if parcelas else (None, "")
        return out.Pagamento(
            valor=plan.amount,
            preco_pix=plan.pix,
            parcelas=[out.Parcela(vezes=i.n, valor=i.value) for i in plan.installments],
            max_parcelas=plan.max_installments,
            combinacao_permitida=plan.combination_allowed,
            parcelas_solicitadas_ok=ok,
            resposta=f"{msg} {plan.describe()}".strip(),
        )

    @mcp.tool(annotations=READ_ONLY)
    @_logged
    def calcular_frete(
        valor_pedido: float, cidade: str, produto_ou_categoria: str = ""
    ) -> out.Frete:
        """Opções de frete para o valor do pedido e a cidade de destino.

        Campo Grande: motoboy, grátis a partir de R$ 500. Outras cidades: SEDEX, Jadlog e PAC, com
        valor pelo CEP. Instrumentos grandes podem exigir frete especial.
        """
        if valor_pedido <= 0:
            raise ToolError("Informe o valor do pedido em reais.")
        category = detect_category(produto_ou_categoria) if produto_ou_categoria else None
        q = shipping_quote(
            valor_pedido, cidade, category_id=category, product_name=produto_ou_categoria
        )
        return out.Frete(
            regiao_metropolitana=q.metro,
            opcoes=[
                out.OpcaoFrete(modalidade=o.carrier, prazo=o.days, valor=o.price) for o in q.options
            ],
            frete_especial=q.special_freight,
            resposta=q.describe(),
        )

    @mcp.tool(annotations=READ_ONLY)
    @_logged
    def status_loja() -> out.StatusLoja:
        """A loja está aberta agora? Horário de funcionamento e próxima abertura.

        Considera o fuso de Campo Grande e os feriados.
        """
        st = store_status(now() if now else None)
        return out.StatusLoja(
            aberta=st.is_open,
            agora=st.now.isoformat(timespec="minutes"),
            fecha_as=st.closes_at.strftime("%H:%M") if st.closes_at else None,
            proxima_abertura=st.next_opening.isoformat(timespec="minutes")
            if st.next_opening
            else None,
            motivo=st.reason,
            horario_semanal=weekly_hours(),
            resposta=st.describe(),
            orientacao=SPECIAL_HOURS_NOTE,
        )

    @mcp.tool(annotations=READ_ONLY)
    @_logged
    def consultar_politica(pergunta: str, limite: int = 2) -> out.ConsultaPolitica:
        """Trechos do manual de políticas relevantes para a pergunta.

        Cobre trocas e devoluções, pagamento, frete e entregas, promoções, garantia, horário,
        contato e endereço, e privacidade (LGPD).
        """
        hits = retriever.search(pergunta, k=max(1, min(limite, 4)), kind="policy")
        sections = [s for h in hits if (s := policies.get(h.doc_id))]
        return out.ConsultaPolitica(
            secoes=[
                out.SecaoPolitica(
                    id=s.id,
                    titulo=s.title,
                    tipo=s.kind,
                    tokens_estimados=s.tokens_est,
                    conteudo=s.body,
                )
                for s in sections
            ],
            orientacao=None
            if sections
            else "Nenhuma seção do manual cobre essa pergunta. Não invente regras: diga que vai "
            "verificar com a equipe ou que o assunto foge do atendimento da loja.",
        )

    # ------------------------------------------------------------------ resources

    @mcp.resource(
        "politicas://indice",
        name="indice_politicas",
        description="Seções do manual com tipo e custo estimado em tokens",
        mime_type="application/json",
    )
    def politicas_indice() -> str:
        return json.dumps(
            [
                {"id": s.id, "titulo": s.title, "tipo": s.kind, "secoes_pdf": list(s.pdf_refs),
                 "tokens_estimados": s.tokens_est, "uri": f"politicas://{s.id}"}
                for s in policies.index()
            ],
            ensure_ascii=False,
        )  # fmt: skip

    @mcp.resource(
        "politicas://{secao}",
        name="secao_politica",
        description="Uma seção do manual de políticas (markdown curado)",
        mime_type="text/markdown",
    )
    def politicas_secao(secao: str) -> str:
        s = policies.get(secao)
        if s is None:
            valid = ", ".join(x.id for x in policies.index())
            raise ResourceError(f"Seção '{secao}' não existe. Seções: {valid}")
        return f"# {s.title}\n\n{s.body}"

    @mcp.resource(
        "catalogo://categorias",
        name="categorias",
        description="Categorias do catálogo com a quantidade de produtos",
        mime_type="application/json",
    )
    def catalogo_categorias() -> str:
        return json.dumps(
            [{"id": c.category_id, "nome": c.name, "produtos": c.product_count}
             for c in catalog.categories()],
            ensure_ascii=False,
        )  # fmt: skip

    # ------------------------------------------------------------------ prompt

    @mcp.prompt(
        name="persona_atendente",
        description="Persona 'Mel' e regras de atendimento da Empório da Música (manual §7)",
    )
    def persona_atendente(nome_cliente: str = "") -> str:
        return persona_prompt(nome_cliente or None)

    return mcp
