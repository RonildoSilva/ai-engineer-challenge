"""Pré-busca: o host chama as tools do assunto detectado antes de consultar o modelo.

O modelo recebe os resultados como se ele mesmo tivesse chamado as tools (uma mensagem de
assistant com `tool_calls` seguida das mensagens `tool`). Assim, na maioria dos turnos basta
uma chamada ao LLM, sem as definições das tools no prompt: menos tokens, menos latência e
nenhuma chance de o modelo responder preço "de cabeça".

As chamadas passam pelo MCP (o mesmo caminho das chamadas do modelo) e são encadeadas quando
uma depende da outra: o preço do produto encontrado alimenta a simulação de pagamento e o frete.

O que vai ao modelo é uma versão enxuta da saída: os textos `resposta`/`resumo_preco` já
redigidos pelas regras do manual (preço com promoção e PIX), a promoção explícita ("nenhuma
ativa") e as orientações. Campos numéricos soltos, que o modelo poderia rotular errado, ficam de
fora.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from emporio.agent.budget import ContextBudget
from emporio.agent.mcp_client import McpToolbox, ToolResult
from emporio.agent.router import TOOLS, Intent, Route, policy_terms, product_query

ASK_ORDER_DATA = (
    "Para consultar um pedido, peça ao cliente o número do pedido e o e-mail ou telefone "
    "cadastrado (proteção de dados). Não confirme se o pedido existe antes disso."
)
ASK_ORDER_DATA_RETURN = (
    "Responda com os prazos e as condições de troca e devolução que vieram em "
    "`consultar_politica` (por exemplo, o prazo de arrependimento). Se o cliente quiser avaliar "
    "uma compra específica, peça o número do pedido e o e-mail ou telefone cadastrado."
)


def ask_order_data(order_id: str | None, contact: str | None) -> str:
    """Pede o que falta para consultar o pedido (verificação do titular, LGPD)."""
    if order_id:
        return (
            f"Certo, pedido {order_id}! Para confirmar que é você, me informa o e-mail ou o "
            "telefone usado na compra?"
        )
    if contact:
        return "Obrigada! Agora me passa o número do pedido, por favor?"
    return (
        "Claro, vou verificar pra você! Me passa o número do pedido e o e-mail ou telefone "
        "usado na compra? 😊"
    )


USE_DATA = (
    "Os dados abaixo já foram consultados nas ferramentas da loja para esta mensagem. Responda "
    "com base neles, usando os preços e textos exatamente como vieram (campos `resposta` e "
    "`preco`); não acrescente valores, prazos ou regras que não estejam ali."
)
# O Bonsai chamou o preço do PIX de "promocional": a ausência de promoção vai explícita.
NO_PROMOTION = "nenhuma (o desconto do PIX é forma de pagamento, não promoção)"
AVAILABILITY = {
    "disponivel": "disponível",
    "esgotado": "esgotado (sem estoque no momento)",
    "descontinuado": "descontinuado (não é mais vendido)",
    "em_breve": "em breve (ainda não está à venda)",
}
# palavras do assunto somadas à pergunta na busca do manual ("dá pra fazer em 10x?" sozinho não
# casa com nenhuma seção; "pagamento parcelamento …" casa com a de pagamento)
POLICY_TOPIC = {
    Intent.POLITICA: "",
    Intent.DEVOLUCAO: "troca devolução",
    Intent.PAGAMENTO: "pagamento parcelamento",
    Intent.FRETE: "frete entrega",
}
COMPLAINT_REPLY = (
    "Sinto muito por isso! 😔 Registrei sua mensagem e encaminhei para a equipe da Empório da "
    "Música, que retorna em até 24 horas úteis. Se preferir, fale também pelo WhatsApp "
    "(67) 3321-4500. Se for sobre um pedido, me passa o número do pedido e o e-mail ou telefone "
    "da compra que eu já deixo anotado."
)
COMPLAINT = (
    "O cliente está insatisfeito ou pediu para falar com uma pessoa. Acolha com empatia, sem "
    "discutir nem se justificar, diga que o caso foi registrado e encaminhado à equipe, que "
    "retorna em até 24 horas úteis, e informe o WhatsApp (67) 3321-4500. Se ele citar um pedido "
    "sem os dados, peça o número do pedido e o e-mail ou telefone da compra."
)


DISCOUNT_WORDS = frozenset({"desconto", "descontos", "promocao", "promocoes", "oferta", "ofertas"})


CONTACT_WORDS = frozenset({
    "endereco", "onde fica", "localizacao", "como chegar", "telefone", "whatsapp", "contato",
    "email da loja", "instagram", "site",
})  # fmt: skip
_CONTACT_FIELDS = (("Endereço", "Endereço"), ("Telefone", "Telefone"),
                   ("WhatsApp", "WhatsApp (atendimento)"), ("E-mail", "E-mail"))  # fmt: skip


def contact_reply(data: dict[str, Any]) -> str | None:
    """Resposta pronta de contato, lida da tabela da seção "sobre_contato" do manual (via MCP)."""
    section = next((s for s in data.get("secoes", []) if s.get("id") == "sobre_contato"), None)
    if section is None:
        return None
    rows = {}
    for line in section.get("conteudo", "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2:  # noqa: PLR2004 (tabela campo | valor)
            rows[cells[0]] = cells[1]
    found = [
        f"{label}: {value}"
        for prefix, label in _CONTACT_FIELDS
        if (value := next((v for k, v in rows.items() if k.startswith(prefix)), None))
    ]
    if len(found) < 2:  # noqa: PLR2004 (tabela não reconhecida: deixa o modelo responder)
        return None
    return "Aqui estão os contatos da Empório da Música 🎸 " + " · ".join(found) + "."


def not_carried_reply(item: str | None, categories: str | None) -> str:
    """Instrumento que a loja não trabalha: resposta pronta, com o que ela tem."""
    what = item or "esse instrumento"
    offer = f" Nosso catálogo tem {categories.lower()}." if categories else ""
    return (
        f"No momento a Empório da Música não trabalha com {what}.{offer} Posso te ajudar a "
        "escolher algum deles? 🎶"
    )


def hours_reply(data: dict[str, Any]) -> str:
    """Resposta pronta de horário: situação agora + horário da semana + datas especiais."""
    parts = [data.get("resposta", ""), f"Nosso horário: {data.get('horario_semanal', '')}"]
    if note := data.get("orientacao"):
        parts.append(note)
    return " ".join(p.strip() for p in parts if p).strip()


def accessory_reply(item: str | None) -> str:
    """Resposta pronta para pedido só de acessório (o Bonsai recitava a orientação interna)."""
    # o termo vem como o cliente escreveu ("palheta", "afinador"): "não vendemos palheta" soa
    # natural; pluralizar à mão errava ("afinadors")
    what = f"{item} nem outros acessórios" if item else "acessórios"
    return (
        f"Aqui na Empório da Música a gente trabalha só com instrumentos musicais, então não "
        f"vendemos {what} (cordas, cabos, cases, pedais, amplificadores). "
        "Mas se você estiver procurando um instrumento, posso te ajudar a escolher! 🎸"
    )


@dataclass(frozen=True)
class PrefetchCall:
    name: str
    arguments: dict[str, Any]
    result: ToolResult
    content: str  # o que vai ao modelo (versão enxuta)


@dataclass
class Prefetch:
    calls: list[PrefetchCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # orientações para este turno
    unresolved: set[Intent] = field(default_factory=set)  # intenções sem dados suficientes
    # resposta pronta, sem LLM: só falta o cliente informar dados (o modelo pequeno repetia a
    # orientação interna para o cliente em vez de pedir os dados)
    direct_answer: str | None = None
    # (valor, em promoção) do produto encontrado neste turno: o agente o guarda para perguntas
    # seguintes sem o nome do produto ("e dá pra fazer em 10x?")
    product_price: tuple[float, bool] | None = None

    def messages(self) -> list[dict[str, Any]]:
        """Mensagens de chat no formato de uma chamada de tools já respondida."""
        if not self.calls:
            return []
        ids = [f"pre{i}" for i in range(1, len(self.calls) + 1)]
        call_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": cid,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments, ensure_ascii=False),
                    },
                }
                for cid, c in zip(ids, self.calls, strict=True)
            ],
        }
        results = [
            {"role": "tool", "tool_call_id": cid, "content": c.content}
            for cid, c in zip(ids, self.calls, strict=True)
        ]
        return [call_msg, *results]

    @property
    def evidence_text(self) -> str:
        """Tudo o que as tools devolveram (saída completa), para conferir valores da resposta."""
        return "\n".join(c.result.text for c in self.calls)


async def run_prefetch(
    route: Route,
    text: str,
    toolbox: McpToolbox,
    budget: ContextBudget,
    context_price: tuple[float, bool] | None = None,
) -> Prefetch:
    """Executa as chamadas do assunto detectado, encadeando valores quando preciso.

    `context_price`: preço do último produto encontrado na conversa, usado por pagamento/frete
    quando a mensagem não traz produto nem valor.
    """
    planner = _Planner(route, text, toolbox, budget)
    planner.context_price = context_price
    return await planner.run()


class _Planner:
    def __init__(self, route: Route, text: str, toolbox: McpToolbox, budget: ContextBudget):
        # numa mensagem só com dados ("leticia@…"), o assunto é o da mensagem que o abriu
        text = route.topic if route.inherited and route.topic else text
        self.route, self.text, self.toolbox, self.budget = route, text, toolbox, budget
        self.pf = Prefetch()
        self.price: tuple[float, bool] | None = None  # (valor, em promoção) do produto achado
        self.context_price: tuple[float, bool] | None = None  # de um turno anterior
        self.policy_topics: list[Intent] = [Intent.POLITICA] if route.has(Intent.POLITICA) else []
        if route.has(Intent.RECLAMACAO):
            self.pf.notes.append(COMPLAINT)
            if route.intents == (Intent.RECLAMACAO,):
                # o Bonsai devolveu a orientação palavra por palavra: aqui a resposta é pronta
                self.pf.direct_answer = COMPLAINT_REPLY

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        r = await self.toolbox.call(name, args)
        self.pf.calls.append(PrefetchCall(name, args, r, _condense(name, r, self.budget)))
        return r

    async def run(self) -> Prefetch:
        r, pf = self.route, self.pf
        await self.products()
        await self.orders()
        await self.money()
        if r.has(Intent.HORARIO):
            res = await self.call("status_loja", {})
            if r.intents == (Intent.HORARIO,) and res.ok and res.data:
                # dado puro: "abrem no domingo?" pede o horário semanal, que o Bonsai ignorava
                self.pf.direct_answer = hours_reply(res.data)
        if self._payment_answered_policy():
            self.policy_topics.remove(Intent.POLITICA)
        if self.policy_topics:
            topics = " ".join(POLICY_TOPIC[i] for i in self.policy_topics)
            limit = max(self.budget.policy_sections, min(len(self.policy_topics), 3))
            res = await self.call(
                "consultar_politica", {"pergunta": f"{topics} {self.text}".strip(), "limite": limit}
            )
            if self._contact_only() and (reply := contact_reply(res.data or {})):
                # dado puro, como o horário: o Bonsai trocava telefone e WhatsApp e chegou a
                # inventar valores numa resposta de endereço
                self.pf.direct_answer = reply
        if pf.calls:
            pf.notes.insert(0, USE_DATA)
        if r.has(Intent.GERAL):
            pf.unresolved.add(Intent.GERAL)
        pf.unresolved |= {i for i in r.intents if _failed(pf, i)}
        return pf

    def _contact_only(self) -> bool:
        terms = set(policy_terms(self.text))
        return self.route.intents == (Intent.POLITICA,) and bool(terms) and terms <= CONTACT_WORDS

    def _payment_answered_policy(self) -> bool:
        """Pergunta de desconto com produto em contexto: a simulação de pagamento já responde.

        O manual junto (pagamento + promoções) fazia o Bonsai despejar as seções e chamar o PIX de
        "preço promocional". Só vale quando a política veio só de palavras de desconto/promoção.
        """
        simulated = any(c.name == "simular_pagamento" and c.result.ok for c in self.pf.calls)
        terms = set(policy_terms(self.text))
        return simulated and Intent.POLITICA in self.policy_topics and terms <= DISCOUNT_WORDS

    async def products(self) -> None:
        r, text, limit = self.route, self.text, self.budget.product_results
        if r.has(Intent.ACESSORIO):
            res = await self.call("buscar_produtos", {"texto": text})
            if r.intents == (Intent.ACESSORIO,):
                item = ((res.data or {}).get("filtros") or {}).get("acessorio")
                self.pf.direct_answer = accessory_reply(item)
        found = False
        if r.has(Intent.PRODUTO):
            res = await self.call("detalhar_produto", {"produto": product_query(text)})
            data = res.data or {}
            if data.get("situacao") == "found" and (p := data.get("produto")):
                found = True
                self.price = (float(p["preco_final"]), bool(p.get("promocao")))
                self.pf.product_price = self.price
            elif data.get("situacao") == "not_found" and not r.has(Intent.CATALOGO):
                await self.call(
                    "buscar_produtos", {"texto": text, "limite": limit, "detalhe": "resumo"}
                )
        if r.has(Intent.CATALOGO) and not found:
            res = await self.call(
                "buscar_produtos", {"texto": text, "limite": limit, "detalhe": "resumo"}
            )
            data = res.data or {}
            if r.intents == (Intent.CATALOGO,) and data.get("situacao") == "not_carried":
                f = data.get("filtros") or {}
                self.pf.direct_answer = not_carried_reply(f.get("instrumento"), f.get("categorias"))

    async def orders(self) -> None:
        r, s = self.route, self.route.slots
        identity = {"numero_pedido": s.order_id, "email_ou_telefone": s.contact}
        if r.has(Intent.PEDIDO):
            if s.has_order_identity:
                await self.call("consultar_pedido", identity)
            else:
                self.pf.notes.append(ASK_ORDER_DATA)
                if r.intents == (Intent.PEDIDO,):
                    self.pf.direct_answer = ask_order_data(s.order_id, s.contact)
        if r.has(Intent.DEVOLUCAO):
            if s.has_order_identity:
                await self.call("verificar_devolucao", {**identity, "motivo": self.text})
            else:
                self.policy_topics.append(Intent.DEVOLUCAO)
                self.pf.notes.append(ASK_ORDER_DATA_RETURN)
                if r.intents == (Intent.DEVOLUCAO,) and (s.order_id or s.contact):
                    # já começou a informar os dados: pede só o que falta, sem LLM
                    self.pf.direct_answer = ask_order_data(s.order_id, s.contact)

    async def money(self) -> None:
        """Pagamento e frete: com valor (do produto ou citado) calcula; sem valor, a política."""
        r, s, price = self.route, self.route.slots, self.price
        mentions_product = r.has(Intent.PRODUTO) or r.has(Intent.CATALOGO)
        if price is None and s.amount is None and not mentions_product:
            price = self.context_price  # "e dá pra fazer em 10x?" logo depois do GD20
        # numa busca por faixa ("até R$ 1000") o valor citado é filtro, não o valor da compra
        amount = price[0] if price else (None if r.has(Intent.CATALOGO) else s.amount)
        if r.has(Intent.PAGAMENTO):
            if amount:
                args: dict[str, Any] = {
                    "valor": amount,
                    "preco_promocional": bool(price and price[1]),
                }
                if s.installments:
                    args["parcelas"] = s.installments
                await self.call("simular_pagamento", args)
            else:
                self.policy_topics.append(Intent.PAGAMENTO)
        if r.has(Intent.FRETE):
            if amount and s.city:
                await self.call(
                    "calcular_frete",
                    {"valor_pedido": amount, "cidade": s.city, "produto_ou_categoria": self.text},
                )
            else:
                self.policy_topics.append(Intent.FRETE)


def _failed(pf: Prefetch, intent: Intent) -> bool:
    names = set(TOOLS[intent])
    return any(c.name in names and not c.result.ok for c in pf.calls)


# ------------------------------------------------------------------------ versão enxuta


def _condense(name: str, r: ToolResult, budget: ContextBudget) -> str:
    """Saída da tool reduzida ao que o modelo precisa para redigir a resposta."""
    if not r.ok or r.data is None:
        return r.text
    d = r.data
    out: dict[str, Any]
    match name:
        case "detalhar_produto":
            out = {"situacao": d.get("situacao")}
            if p := d.get("produto"):
                out["produto"] = _product(p, full=True)
            if c := d.get("candidatos"):
                out["candidatos"] = [_product(x) for x in c]
            if a := d.get("alternativas"):
                out["alternativas"] = [_product(x) for x in a]
        case "buscar_produtos":
            out = {
                "situacao": d.get("situacao"),
                "filtros": d.get("filtros"),
                "produtos": [_product(x) for x in d.get("produtos", [])],
            }
        case "consultar_politica":
            # seções "guideline" são instruções ao atendente, não informação para o cliente: o
            # Bonsai as recitava. A conduta vai nas orientações do turno.
            policy = [s for s in d.get("secoes", []) if s.get("tipo") != "guideline"]
            sections = budget.pick_sections(policy)
            out = {
                "secoes": [
                    {"titulo": s["titulo"], "conteudo": _without_curation_notes(s["conteudo"])}
                    for s in sections
                ]
            }
        case _:
            out = dict(d)
    if o := d.get("orientacao"):
        out["orientacao"] = o
    return json.dumps({k: v for k, v in out.items() if v not in (None, [], {})},
                      ensure_ascii=False, separators=(",", ":"))  # fmt: skip


def _without_curation_notes(body: str) -> str:
    """Tira as notas de curadoria (decisões de manutenção, não texto para o cliente).

    Uma nota é um bloco de citação que começa com "> **Nota de curadoria:**".
    """
    out: list[str] = []
    in_note = False
    for line in body.splitlines():
        if line.startswith("> **Nota de curadoria"):
            in_note = True
        elif in_note and not line.startswith(">"):
            in_note = False
        if not in_note:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _product(p: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "nome": p.get("nome"),
        "disponibilidade": AVAILABILITY.get(p.get("disponibilidade", ""), p.get("disponibilidade")),
        "preco": p.get("resumo_preco"),
        "promocao": p.get("promocao") or NO_PROMOTION,
    }
    if full:
        if specs := p.get("especificacoes"):
            item["especificacoes"] = specs
        if desc := p.get("descricao"):
            item["descricao"] = desc
    return item
