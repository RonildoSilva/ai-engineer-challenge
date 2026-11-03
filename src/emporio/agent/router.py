"""Detecção de assunto: o que o cliente quer, quais dados já informou e quais tools importam.

Determinístico e barato (palavras-chave sobre o texto normalizado), aplicado a todos os modelos:
- uma mensagem pode ter várias intenções ("quanto custa o GD20 e dá pra fazer em 10x?");
- os dados citados (número do pedido, e-mail/telefone, valor, parcelas, cidade) viram `Slots`;
- cada intenção traz um subconjunto pequeno de tools, em vez das 8, o que encurta o prompt;
- mensagem só com dados ("é o pedido 1042") herda as intenções do turno anterior.

O roteador não responde nada: ele só decide o que pré-buscar (`prefetch.py`) e o que oferecer
ao modelo. Se errar, o modelo ainda tem as tools do subconjunto para corrigir o rumo.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from emporio.data.rules import brands_in, normalize
from emporio.domain.catalog import detect_accessory, detect_category, extract_price_range


class Intent(StrEnum):
    PRODUTO = "produto"  # um modelo específico ("Takamine GD20", "quanto custa o …")
    CATALOGO = "catalogo"  # opções por categoria/faixa ("violões até R$ 1000")
    ACESSORIO = "acessorio"  # cordas, palhetas… (a loja não vende)
    PEDIDO = "pedido"  # status/rastreio de um pedido
    DEVOLUCAO = "devolucao"  # troca, devolução, arrependimento, defeito
    PAGAMENTO = "pagamento"  # PIX, cartão, boleto, parcelas
    FRETE = "frete"  # entrega, prazo, frete grátis
    HORARIO = "horario"  # aberto agora? horário, feriados
    POLITICA = "politica"  # contato, endereço, garantia, privacidade, promoções
    RECLAMACAO = "reclamacao"  # insatisfação ou pedido de atendimento humano: vai para a equipe
    SAUDACAO = "saudacao"  # só cumprimento ou agradecimento
    GERAL = "geral"  # nada reconhecido: o modelo decide com as tools genéricas


# Palavras (texto normalizado, sem acento) que indicam cada intenção. Expressões com espaço
# casam como frase; termos de uma palavra casam como palavra inteira.
KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.PEDIDO: (
        "pedido", "pedidos", "encomenda", "rastreio", "rastreamento", "rastrear",
        "codigo de rastreio", "minha compra", "meu pedido", "nao chegou", "ainda nao chegou",
        "status da compra", "foi enviado", "ja enviaram", "ja foi entregue", "cade",
    ),
    Intent.DEVOLUCAO: (
        "troca", "trocas", "trocar", "devolver", "devolucao", "devolucoes", "devolvo",
        "arrependi", "arrependimento", "arrependido", "arrependida", "defeito", "defeituoso",
        "estragado", "quebrado", "quebrada", "veio errado", "estorno", "reembolso",
        "cancelar", "cancelamento", "dinheiro de volta",
    ),
    Intent.PAGAMENTO: (
        "pagamento", "pagar", "pago", "pix", "cartao", "credito", "debito", "boleto",
        "parcela", "parcelas", "parcelar", "parcelado", "parcelamento", "vezes", "juros",
        "a vista", "formas de pagamento",
    ),
    Intent.FRETE: (
        "frete", "fretes", "entrega", "entregas", "entregam", "entregar", "envio", "enviam",
        "enviar", "envia", "sedex", "pac", "jadlog", "motoboy", "retirar", "retirada",
        "prazo de entrega", "quanto tempo chega", "chega em",
    ),
    Intent.HORARIO: (
        "horario", "horarios", "abre", "abrem", "aberto", "aberta", "fecha", "fecham",
        "fechado", "fechada", "funciona", "funcionamento", "feriado", "feriados", "domingo",
        "sabado", "que horas", "expediente",
    ),
    Intent.POLITICA: (
        "endereco", "onde fica", "localizacao", "como chegar", "telefone", "whatsapp",
        "contato", "email da loja", "instagram", "site", "garantia", "garantias", "conserto",
        "assistencia", "privacidade", "dados pessoais", "meus dados", "lgpd", "promocao",
        "promocoes", "desconto", "descontos", "oferta", "ofertas", "politica", "politicas",
    ),
    Intent.RECLAMACAO: (
        "reclamacao", "reclamar", "reclamando", "reclamei", "pessimo", "pessima", "absurdo",
        "insatisfeito", "insatisfeita", "descaso", "procon", "decepcionado", "decepcionada",
        "horrivel", "vergonha", "desrespeito", "nunca mais compro", "palhacada",
        "atendente", "humano", "falar com alguem", "falar com uma pessoa", "gerente",
    ),
}  # fmt: skip

# Pedido de preço/informação de um item ("quanto custa o…", "tem o…").
PRICE_WORDS = ("quanto custa", "quanto ta", "quanto esta", "preco", "valor", "custa", "custam")
CATALOG_WORDS = (
    "instrumento", "instrumentos", "opcoes", "opcao", "indicacao", "indica", "recomenda",
    "recomendacao", "iniciante", "iniciantes", "catalogo", "modelos", "sugestao", "sugere",
)  # fmt: skip
GREETINGS = frozenset({
    "oi", "ola", "bom", "boa", "dia", "tarde", "noite", "obrigado", "obrigada", "valeu",
    "tudo", "bem", "td", "blz", "beleza", "e", "ai", "opa", "tchau", "ate", "mais", "logo",
    "mel", "brigado", "brigada", "muito", "ok", "certo", "show", "perfeito",
})  # fmt: skip

# Tools de cada intenção (o modelo só recebe as do assunto detectado).
TOOLS: dict[Intent, tuple[str, ...]] = {
    Intent.PRODUTO: ("detalhar_produto", "buscar_produtos"),
    Intent.CATALOGO: ("buscar_produtos", "detalhar_produto"),
    Intent.ACESSORIO: (),
    Intent.PEDIDO: ("consultar_pedido",),
    Intent.DEVOLUCAO: ("verificar_devolucao", "consultar_politica"),
    Intent.PAGAMENTO: ("simular_pagamento", "consultar_politica"),
    Intent.FRETE: ("calcular_frete", "consultar_politica"),
    Intent.HORARIO: ("status_loja",),
    Intent.POLITICA: ("consultar_politica",),
    Intent.RECLAMACAO: ("consultar_politica",),
    Intent.SAUDACAO: (),
    Intent.GERAL: ("buscar_produtos", "detalhar_produto", "consultar_politica"),
}

# --------------------------------------------------------------------------- dados citados

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[\s.-]?\d{4}\b")
# sobre o texto de `normalize_keep_symbols` ("pedido nº 1042" → "pedido numero 1042")
_ORDER = re.compile(
    r"\b(?:pedido|compra|encomenda|numero|n)\s+(?:numero\s+|n\s+|de\s+)?(\d{1,7})\b"
)
_INSTALLMENTS = re.compile(r"\b(\d{1,2})\s*(?:x|vezes)\b", re.IGNORECASE)
_AMOUNT = re.compile(r"r\$\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?", re.IGNORECASE)
_CITY = re.compile(
    r"\b(?:para|pra|em|p/)\s+((?:[A-ZÀ-Ú][\wÀ-ú]+)(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][\wÀ-ú]+){0,3})"
)
_NOT_CITIES = frozenset({"PIX", "Takamine", "Yamaha", "Fender", "Gibson"})


@dataclass(frozen=True)
class Slots:
    order_id: str | None = None
    contact: str | None = None  # e-mail ou telefone do titular
    amount: float | None = None  # valor em reais citado ("R$ 1.500")
    installments: int | None = None  # "10x", "em 12 vezes"
    city: str | None = None

    def merged(self, older: Slots) -> Slots:
        """Completa o que falta com dados de mensagens anteriores (o atual prevalece)."""
        return Slots(
            self.order_id or older.order_id,
            self.contact or older.contact,
            self.amount if self.amount is not None else older.amount,
            self.installments or older.installments,
            self.city or older.city,
        )

    @property
    def has_order_identity(self) -> bool:
        return bool(self.order_id and self.contact)


def extract_slots(text: str) -> Slots:
    email = _EMAIL.search(text)
    rest = _EMAIL.sub(" ", text)
    order = _ORDER.search(normalize_keep_symbols(rest))
    if order:  # o número do pedido não é telefone
        rest = rest.replace(order.group(1), " ", 1)
    phone = None if email else _PHONE.search(rest)
    amount = _AMOUNT.search(text)
    city = next(
        (m.group(1) for m in _CITY.finditer(text) if m.group(1).split()[0] not in _NOT_CITIES),
        None,
    )
    inst = _INSTALLMENTS.search(text)
    return Slots(
        order_id=order.group(1) if order else None,
        contact=email.group(0) if email else (phone.group(0).strip() if phone else None),
        amount=_brl(amount) if amount else None,
        installments=int(inst.group(1)) if inst and int(inst.group(1)) > 1 else None,
        city=city,
    )


def normalize_keep_symbols(text: str) -> str:
    """Minúsculas sem acento, mas preservando º, # e números (para achar "pedido nº 1042")."""
    for symbol in ("#", "nº", "n°", "Nº", "N°", "n.", "N."):
        text = text.replace(symbol, " numero ")
    return normalize(text)


def _brl(m: re.Match[str]) -> float:
    return float(m.group(1).replace(".", "") + (f".{m.group(2)}" if m.group(2) else ""))


# --------------------------------------------------------------------------- roteamento


@dataclass(frozen=True)
class Route:
    intents: tuple[Intent, ...]
    slots: Slots = field(default_factory=Slots)
    inherited: bool = False  # intenções vieram do turno anterior

    @property
    def tools(self) -> tuple[str, ...]:
        """Subconjunto de tools das intenções, sem repetição, na ordem de relevância."""
        return tuple(dict.fromkeys(t for i in self.intents for t in TOOLS[i]))

    def has(self, intent: Intent) -> bool:
        return intent in self.intents


def _has_any(t: str, terms: Sequence[str]) -> list[str]:
    padded = f" {t} "
    return [k for k in terms if f" {k} " in padded]


_MODEL_CODE = re.compile(r"\b(?=[a-z]*\d)(?=\d*[a-z])[a-z0-9]{3,}\b")  # gd20, yas280, ct60


def detect_intents(text: str) -> tuple[Intent, ...]:
    """Intenções da mensagem, em ordem de prioridade (pode ser vazia)."""
    t = normalize(text)
    found: list[Intent] = []
    for intent in (Intent.PEDIDO, Intent.DEVOLUCAO, Intent.PAGAMENTO, Intent.FRETE,
                   Intent.HORARIO, Intent.POLITICA, Intent.RECLAMACAO):  # fmt: skip
        terms = KEYWORDS[intent]
        if intent is Intent.POLITICA and extract_slots(text).contact:
            terms = tuple(k for k in terms if k not in _CONTACT_WORDS)  # "meu telefone é …"
        if _has_any(t, terms):
            found.append(intent)
    if _INSTALLMENTS.search(text) and Intent.PAGAMENTO not in found:
        found.append(Intent.PAGAMENTO)  # "dá pra fazer em 10x?"
    if Intent.DEVOLUCAO in found and Intent.PEDIDO in found:
        found.remove(Intent.PEDIDO)  # a avaliação da devolução já consulta o pedido
    found[:0] = _product_intents(text, t, service=bool(set(found) & _SERVICE))
    if not found and t and set(t.split()) <= GREETINGS:
        found.append(Intent.SAUDACAO)
    return tuple(dict.fromkeys(found))


_CONTACT_WORDS = frozenset({"telefone", "whatsapp", "contato", "email da loja"})
# assuntos em que citar a categoria não é pedir opções ("meu violão veio com defeito")
_SERVICE = frozenset({Intent.PEDIDO, Intent.DEVOLUCAO, Intent.FRETE, Intent.RECLAMACAO})


def _product_intents(text: str, t: str, *, service: bool) -> list[Intent]:
    if detect_accessory(text):
        return [Intent.ACESSORIO]
    found: list[Intent] = []
    specific = bool(brands_in(text) or _MODEL_CODE.search(_drop_slot_tokens(text)))
    if specific:
        found.append(Intent.PRODUTO)
    wants_options = extract_price_range(text) != (None, None) or _has_any(t, CATALOG_WORDS)
    if wants_options or (detect_category(text) is not None and not service):
        found.append(Intent.CATALOGO)
    elif not specific and not service and _has_any(t, PRICE_WORDS):
        found.append(Intent.PRODUTO)  # "quanto custa aquele modelo preto?" → tenta achar o item
    return found


_CLAUSE = re.compile(r"[,;?!.]|\s(?:e|mas|tambem|também)\s", re.IGNORECASE)


def product_query(text: str) -> str:
    """Trecho da mensagem que cita o produto, para a busca por nome não se perder na frase.

    "quanto custa o GD20 e dá pra fazer em 10x?" → "quanto custa o GD20".
    """
    for clause in _CLAUSE.split(text):
        if brands_in(clause) or _MODEL_CODE.search(_drop_slot_tokens(clause)):
            return clause.strip()
    return text


def _drop_slot_tokens(text: str) -> str:
    """Texto normalizado sem e-mails, '10x' e '18h', para procurar códigos de modelo."""
    return re.sub(r"\b\d{1,2}(?:x|h\d{0,2})\b", " ", normalize(_EMAIL.sub(" ", text)))


_NEEDS_ORDER = frozenset({Intent.PEDIDO, Intent.DEVOLUCAO})
_BARE_NUMBER = re.compile(r"(?<![\d(+])\b(\d{1,7})\b(?![\d)-])")


def _bare_order_number(text: str, contact: str | None) -> str | None:
    """Número solto, quando o cliente responde ao pedido do número ("é o 1042")."""
    rest = text.replace(contact, " ") if contact else text
    m = _BARE_NUMBER.search(_AMOUNT.sub(" ", rest))
    return m.group(1) if m else None


def route(text: str, previous: Route | None = None, history: Sequence[str] = ()) -> Route:
    """Intenções + dados citados nesta mensagem, completados pelo histórico recente.

    `previous`: rota do turno anterior; se a mensagem só traz dados (número do pedido, e-mail),
    as intenções são herdadas. `history`: mensagens anteriores do cliente (mais recente por
    último), de onde vêm os dados que faltam.
    """
    own = extract_slots(text)
    if own.order_id is None and previous and set(previous.intents) & _NEEDS_ORDER:
        own = replace(own, order_id=_bare_order_number(text, own.contact))  # "é o 1042"
    slots = own
    for older in reversed(history[-4:]):
        slots = slots.merged(extract_slots(older))
    intents = detect_intents(text)
    gave_data = own != Slots()
    if (not intents or intents == (Intent.SAUDACAO,)) and previous and gave_data:
        follow = tuple(i for i in previous.intents if i not in (Intent.SAUDACAO, Intent.GERAL))
        if follow:
            return Route(follow, slots, inherited=True)
    return Route(intents or (Intent.GERAL,), slots)
