"""Domínio do catálogo: preços, busca de produtos, detalhe e alternativas.

Regras do manual aplicadas por código, nunca pelo LLM:
- §6.2 Comunicação: preço promocional sempre junto do original e do percentual.
- §6.2 Cumulatividade: o desconto de 5% no PIX (§3) não vale sobre preço promocional.
- §7.3: produto esgotado ou descontinuado → informar e sugerir alternativas disponíveis.
- §1 e §7.1: a loja não vende acessórios → sinalizar para o agente redirecionar com educação.

Também extrai do texto livre a faixa de preço e a categoria ("violões até R$1000"), para que
uma busca funcione bem mesmo quando o modelo não preenche os filtros da ferramenta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz

from emporio.data.rules import normalize
from emporio.domain.models import Category, Product, Promotion
from emporio.domain.policies import STOPWORDS
from emporio.ports import Repository, Retriever

PIX_DISCOUNT_PERCENT = 5

# ------------------------------------------------------------------------------ preço


def format_brl(value: float) -> str:
    """2199.0 → "R$ 2.199,00"."""
    s = f"{value:,.2f}"
    return "R$ " + s.replace(",", "§").replace(".", ",").replace("§", ".")


@dataclass(frozen=True)
class PriceInfo:
    list_price: float
    final_price: float  # o que o cliente paga (promocional, se houver)
    promotion: Promotion | None
    pix_price: float | None  # None quando há promoção (descontos não acumulam)

    def describe(self) -> str:
        """Texto pronto para o cliente, já seguindo as regras de comunicação do manual."""
        if self.promotion:
            p = self.promotion
            return (
                f"{format_brl(self.final_price)} na promoção {p.label} "
                f"({p.percent}% de desconto sobre {format_brl(self.list_price)}); "
                "o desconto do PIX não se soma à promoção"
            )
        return (
            f"{format_brl(self.list_price)} "
            f"(ou {format_brl(self.pix_price or self.list_price)} no PIX, "
            f"{PIX_DISCOUNT_PERCENT}% de desconto)"
        )


def price_info(product: Product) -> PriceInfo:
    if product.promotion:
        return PriceInfo(product.price_brl, product.promotion.price_brl, product.promotion, None)
    pix = round(product.price_brl * (1 - PIX_DISCOUNT_PERCENT / 100), 2)
    return PriceInfo(product.price_brl, product.price_brl, None, pix)


# ------------------------------------------------------------------------------ texto livre

_NUM = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\s*(mil|k)?"


def _to_float(m: re.Match[str], offset: int = 1) -> float:
    integer, cents, mult = m.group(offset), m.group(offset + 1), m.group(offset + 2)
    value = float(integer.replace(".", "") + (f".{cents}" if cents else ""))
    return value * 1000 if mult else value


def extract_price_range(text: str) -> tuple[float | None, float | None]:
    """Faixa de preço citada no texto: "até R$1000", "entre 500 e 1.200", "acima de 2 mil"."""
    t = text.lower().replace("r$", " ")
    if m := re.search(rf"entre\s+{_NUM}\s+e\s+{_NUM}", t):
        lo, hi = _to_float(m, 1), _to_float(m, 4)
        return min(lo, hi), max(lo, hi)
    hi = lo = None
    if m := re.search(
        rf"(?:até|ate|no máximo|no maximo|abaixo de|menos de|por menos de)\s+{_NUM}", t
    ):
        hi = _to_float(m)
    if m := re.search(
        rf"(?:acima de|mais de|a partir de|pelo menos|no mínimo|no minimo)\s+{_NUM}", t
    ):
        lo = _to_float(m)
    return lo, hi


# termos → categoria (ids de data/raw/categories.csv)
CATEGORY_TERMS: dict[str, int] = {
    "guitarra": 1, "guitarras": 1,
    "baixo": 2, "baixos": 2, "contrabaixo eletrico": 2,
    "bateria": 3, "baterias": 3, "percussao": 3,
    "teclado": 4, "teclados": 4, "piano": 4, "pianos": 4, "sintetizador": 4,
    "violao": 5, "violoes": 5,
    "flauta": 6, "clarinete": 6, "saxofone": 6, "sax": 6, "oboe": 6,
    "trompete": 7, "trombone": 7, "tuba": 7, "trompa": 7,
    "violino": 8, "violinos": 8, "viola de arco": 8, "violoncelo": 8, "cello": 8,
    "contrabaixo acustico": 8,
    "ukulele": 9, "ukuleles": 9, "uke": 9,
}  # fmt: skip


def detect_category(text: str) -> int | None:
    """Categoria citada no texto (a expressão mais longa vence: "contrabaixo acústico" → 8)."""
    t = f" {normalize(text)} "
    found = [(len(term), cid) for term, cid in CATEGORY_TERMS.items() if f" {term} " in t]
    return max(found)[1] if found else None


ACCESSORY_TERMS = (
    "corda", "cordas", "encordoamento", "palheta", "palhetas", "cabo", "cabos", "pedal",
    "pedais", "pedaleira", "amplificador", "amplificadores", "ampli", "caixa amplificada",
    "case", "cases", "capa", "bag", "afinador", "correia", "baqueta", "baquetas",
    "estante", "pele", "peles", "boquilha", "metronomo", "suporte",
)  # fmt: skip
# "violão de 7 cordas" é instrumento, não acessório
_STRING_COUNT = re.compile(r"\b(\d+|quatro|cinco|seis|sete|oito|doze)\s+cordas\b")


_INSTRUMENT = "|".join(sorted((re.escape(t) for t in CATEGORY_TERMS), key=len, reverse=True))


# Instrumentos que existem, mas a loja não trabalha (nenhuma categoria do catálogo os cobre).
# Sem isto, "tem sanfona?" virava uma busca sem casamento, e a busca devolvia os mais baratos.
NOT_CARRIED: dict[str, str] = {
    "sanfona": "sanfona", "sanfonas": "sanfona", "acordeon": "acordeon", "acordeao": "acordeon",
    "acordeom": "acordeon", "gaita": "gaita", "gaitas": "gaita", "cavaquinho": "cavaquinho",
    "cavaquinhos": "cavaquinho", "cavaco": "cavaquinho", "bandolim": "bandolim", "harpa": "harpa",
    "banjo": "banjo", "berimbau": "berimbau", "viola caipira": "viola caipira",
}  # fmt: skip


def detect_not_carried(text: str) -> str | None:
    """Instrumento citado que a loja não vende (e nenhuma categoria do catálogo foi citada)."""
    if detect_category(text) is not None:
        return None
    t = f" {normalize(text)} "
    found = [(len(k), v) for k, v in NOT_CARRIED.items() if f" {k} " in t]
    return max(found)[1] if found else None


def detect_accessory(text: str) -> str | None:
    """Acessório pedido no texto (a loja não vende), ou None.

    - "vocês vendem palheta?" → acessório (nenhum instrumento citado);
    - "corda de violão", "amplificador pra guitarra" → acessório *para* um instrumento;
    - "violão de 7 cordas", "violão com case" → busca de instrumento.
    """
    t = _STRING_COUNT.sub(" ", normalize(text))
    has_instrument = detect_category(text) is not None
    for term in ACCESSORY_TERMS:
        if not re.search(rf"\b{re.escape(term)}\b", t):
            continue
        for_instrument = (
            rf"\b{re.escape(term)}\s+(?:de|para|pra|do|da|p)\s+"
            rf"(?:um |uma |o |a )?(?:{_INSTRUMENT})\b"
        )
        if not has_instrument or re.search(for_instrument, t):
            return term
    return None


# palavras de pedido que não descrevem o produto ("quais opções disponíveis custando…")
_REQUEST_WORDS = frozenset({
    "opcoes", "opcao", "disponivel", "disponiveis", "custando", "custa", "custam", "preco",
    "precos", "valor", "valores", "modelo", "modelos", "algum", "alguma", "alguns", "algumas",
    "vende", "vendem", "venda", "quero", "procuro", "procurando", "gostaria", "tem", "temos",
    "voces", "reais", "real", "ate", "entre", "acima", "abaixo", "menos", "mais", "partir",
    "maximo", "minimo", "mil", "quanto", "quantos", "qual", "quais", "custaria", "sai",
    "saindo", "informacao", "informacoes", "detalhes", "sobre", "esse", "essa",
})  # fmt: skip


def descriptive_terms(text: str) -> set[str]:
    """Palavras que descrevem o produto, sem categoria, preço e palavras de pedido."""
    t = normalize(text)
    for term in sorted(CATEGORY_TERMS, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(term)}\b", " ", t)
    return {
        w for w in t.split()
        if not w.isdigit() and w not in STOPWORDS and w not in _REQUEST_WORDS and len(w) > 1
    }  # fmt: skip


# ------------------------------------------------------------------------------ serviço

Scope = Literal["ok", "accessory", "empty_category", "no_match", "not_carried"]


@dataclass(frozen=True)
class ProductView:
    product: Product
    price: PriceInfo


@dataclass(frozen=True)
class SearchResult:
    scope: Scope
    items: list[ProductView]
    filters: dict[str, object]  # filtros efetivamente aplicados (inclusive os inferidos do texto)
    note: str | None = None  # orientação para o agente comunicar ao cliente


@dataclass(frozen=True)
class ProductDetail:
    status: Literal["found", "ambiguous", "not_found", "accessory"]
    product: ProductView | None = None
    candidates: list[ProductView] = field(default_factory=list)
    alternatives: list[ProductView] = field(default_factory=list)  # se indisponível
    note: str | None = None


ACCESSORY_NOTE = (
    "A Empório da Música trabalha exclusivamente com instrumentos musicais e não vende acessórios "
    "(cordas, palhetas, cabos, cases, pedais, amplificadores). Redirecione com educação e, se "
    "possível, sugira procurar uma loja parceira."
)
UNAVAILABLE_NOTE = {
    "esgotado": "Temporariamente indisponível. Nunca confirme disponibilidade; ofereça as "
    "alternativas disponíveis.",
    "descontinuado": "Não faz mais parte do catálogo. Ofereça modelos equivalentes.",
    "em_breve": "Ainda não está à venda (lançamento em breve). Ofereça alternativas disponíveis.",
}
# palavras de nome que não caracterizam o tipo do instrumento (marca, cor, acabamento)
_GENERIC = {"natural", "sunburst", "preto", "mogno", "cedar", "black", "red", "blue"}

MIN_COMPACT_LEN = 4  # abaixo disso, "partial_ratio" casa com qualquer nome
FOUND_SCORE = 85.0
SINGLE_CANDIDATE_SCORE = 75.0
AMBIGUOUS_SCORE = 60.0
FOUND_MARGIN = 8.0


class CatalogService:
    def __init__(self, repo: Repository, retriever: Retriever | None = None):
        self._repo = repo
        self._retriever = retriever
        self._categories = {c.category_id: c for c in repo.list_categories()}
        self._all = repo.list_products()

    # ------------------------------------------------------------------ consultas

    def categories(self) -> list[Category]:
        return list(self._categories.values())

    def view(self, product: Product) -> ProductView:
        return ProductView(product, price_info(product))

    def search(
        self,
        text: str | None = None,
        *,
        category: int | str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        only_available: bool = True,
        limit: int = 8,
    ) -> SearchResult:
        """Produtos que atendem aos filtros, ordenados por relevância (se houver texto) ou preço.

        Filtros ausentes são inferidos do texto ("violões até R$1000" → categoria 5, ≤ 1000).
        """
        text = (text or "").strip()
        if text and (acc := detect_accessory(text)):
            return SearchResult("accessory", [], {"acessorio": acc}, ACCESSORY_NOTE)
        if text and (item := detect_not_carried(text)):
            sold = ", ".join(c.name for c in self._categories.values() if c.product_count)
            return SearchResult(
                "not_carried", [], {"instrumento": item, "categorias": sold},
                f"A loja não trabalha com {item}. Informe com transparência e apresente as "
                f"categorias do catálogo: {sold}.",
            )  # fmt: skip

        cat_id = self._resolve_category(category) if category is not None else None
        if cat_id is None and text:
            cat_id = detect_category(text)
        if text and min_price is None and max_price is None:
            min_price, max_price = extract_price_range(text)
        filters = {
            "categoria": self._categories[cat_id].name if cat_id else None,
            "preco_min": min_price,
            "preco_max": max_price,
            "apenas_disponiveis": only_available,
        }
        if cat_id and self._categories[cat_id].product_count == 0:
            name = self._categories[cat_id].name
            return SearchResult(
                "empty_category", [], filters,
                f"No momento não há produtos da categoria {name} no catálogo. Informe com "
                "transparência e ofereça ajuda com outras categorias.",
            )  # fmt: skip

        candidates = self._repo.list_products(
            category_id=cat_id,
            min_price=min_price,
            max_price=max_price,
            availability=["disponivel"] if only_available else None,
        )
        ranked = self._rank(text, candidates)
        if self._nothing_matches(text, ranked, cat_id, min_price, max_price):
            return SearchResult(
                "no_match", [], filters,
                "Nada no catálogo corresponde ao que foi pedido. Não ofereça produtos aleatórios: "
                "diga que não encontrou e pergunte mais detalhes ou ofereça as categorias.",
            )  # fmt: skip
        if not ranked:
            return SearchResult("no_match", [], filters, "Nenhum produto atende a esses critérios.")
        return SearchResult("ok", [self.view(p) for p in ranked[:limit]], filters)

    def detail(self, name_or_id: str | int) -> ProductDetail:
        """Um produto pelo id ("95", "p95") ou nome aproximado ("takamine gd 20")."""
        product = self._by_id(name_or_id)
        if product is None:
            query = str(name_or_id)
            if detect_accessory(query):
                return ProductDetail("accessory", note=ACCESSORY_NOTE)
            if detect_category(query) is not None and not descriptive_terms(query):
                return ProductDetail(
                    "not_found",
                    note="A pergunta é sobre uma categoria, não sobre um modelo: use a busca de "
                    "produtos (com filtros de categoria e preço) e apresente as opções.",
                )
            # código de modelo citado ("GF-3D", "GD20") e de um produto só: é ele, sem fuzzy
            product = self._by_model_code(query)
            if product is None:
                found = self._fuzzy_pick(query)
                if isinstance(found, ProductDetail):
                    return found
                product = found
        alternatives = [] if product.is_sellable else self.alternatives(product)
        return ProductDetail(
            "found",
            product=self.view(product),
            alternatives=alternatives,
            note=UNAVAILABLE_NOTE.get(product.availability),
        )

    def alternatives(self, product: Product, limit: int = 3) -> list[ProductView]:
        """Disponíveis da mesma categoria, priorizando o mesmo tipo (palavras do nome) e preço."""
        own = self._type_tokens(product)
        pool = [
            p for p in self._all
            if p.category_id == product.category_id and p.is_sellable
            and p.product_id != product.product_id
        ]  # fmt: skip
        pool.sort(
            key=lambda p: (
                -len(own & self._type_tokens(p)),
                abs(price_info(p).final_price - product.price_brl) / product.price_brl,
            )
        )
        return [self.view(p) for p in pool[:limit]]

    # ------------------------------------------------------------------ apoio

    def _resolve_category(self, category: int | str) -> int | None:
        if isinstance(category, int) or str(category).isdigit():
            cid = int(category)
            return cid if cid in self._categories else None
        by_name = {normalize(c.name): cid for cid, c in self._categories.items()}
        return by_name.get(normalize(str(category))) or detect_category(str(category))

    def _by_id(self, value: str | int) -> Product | None:
        raw = str(value).strip().lower().removeprefix("p")
        return self._repo.get_product(int(raw)) if raw.isdigit() else None

    def _nothing_matches(
        self,
        text: str,
        ranked: list[Product],
        cat_id: int | None,
        min_price: float | None,
        max_price: float | None,
    ) -> bool:
        """Pedido descreve algo, sem filtro de categoria/preço, e a busca não achou nada disso.

        Sem esta checagem, a ordem caía no preço e "tem sanfona?" devolvia ukuleles.
        """
        if not text or cat_id is not None or min_price is not None or max_price is not None:
            return False
        if not descriptive_terms(text) or self._retriever is None:
            return False
        hits = {h.doc_id for h in self._retriever.search(text, k=10, kind="product")}
        return not any(f"p{p.product_id}" in hits for p in ranked)

    def _rank(self, text: str, candidates: list[Product]) -> list[Product]:
        """Ordena pela busca híbrida/lexical; quem ela não ranqueou vem depois, por preço.

        Pedido só de filtros ("violões até R$1000") fica ordenado por preço: as palavras que
        sobram ("opções", "custando") não descrevem o produto e só trariam ruído.
        """
        if not text or self._retriever is None or not descriptive_terms(text):
            return candidates
        order = {
            h.doc_id: i
            for i, h in enumerate(self._retriever.search(text, k=len(self._all), kind="product"))
        }
        return sorted(
            candidates, key=lambda p: (order.get(f"p{p.product_id}", len(order)), p.price_brl)
        )

    def _fuzzy_pick(self, query: str) -> Product | ProductDetail:
        """Vencedor claro do fuzzy, ou o resultado ambíguo / não encontrado."""
        scored = self._fuzzy(query)
        top = scored[0][0] if scored else 0.0
        second = scored[1][0] if len(scored) > 1 else 0.0
        close = [p for s, p in scored[:3] if s >= max(AMBIGUOUS_SCORE, top - 15)]
        if (top >= FOUND_SCORE and top - second >= FOUND_MARGIN) or (
            top >= SINGLE_CANDIDATE_SCORE and len(close) == 1
        ):  # um único candidato próximo: não faz sentido perguntar "qual deles?"
            return scored[0][1]
        if top >= AMBIGUOUS_SCORE:
            return ProductDetail(
                "ambiguous",
                candidates=[self.view(p) for p in close],
                note="Mais de um produto corresponde; pergunte ao cliente qual deles.",
            )
        return ProductDetail(
            "not_found",
            note="Produto não encontrado no catálogo. Não invente: pergunte mais detalhes "
            "ou ofereça buscar por categoria.",
        )

    def _by_model_code(self, query: str) -> Product | None:
        """Produto cujo código de modelo aparece na pergunta, se for um só.

        Código = palavras do nome com dígito, junto da palavra anterior quando o nome as separa
        ("Giannini GF-3D" → "gf 3d"; "Takamine GD20" → "gd20"). O fuzzy sozinho achava o GF-3D
        parecido demais com o GNF-3 e pedia para o cliente escolher.
        """
        q = f" {normalize(query)} "
        hits = []
        for p in self._all:
            words = p.name_normalized.split()
            codes = set()
            for i, w in enumerate(words):
                if any(c.isdigit() for c in w):
                    codes.add(w)
                    if i > 0 and not any(c.isdigit() for c in words[i - 1]):
                        codes.add(f"{words[i - 1]} {w}")
            # só códigos com letra e dígito, ou pares ("gf 3d"): "40" sozinho casaria com preços
            strong = {c for c in codes if " " in c or any(ch.isalpha() for ch in c)}
            if any(f" {c} " in q for c in strong if len(c.replace(" ", "")) >= 3):  # noqa: PLR2004
                hits.append(p)
        return hits[0] if len(hits) == 1 else None

    def _fuzzy(self, query: str) -> list[tuple[float, Product]]:
        # "Quanto custa o Takamine GD20?" → "takamine gd20": palavras de pedido diluem o nome
        q = " ".join(
            w for w in normalize(query).split() if w not in STOPWORDS and w not in _REQUEST_WORDS
        )
        q_compact = q.replace(" ", "")
        if not q:
            return []
        scored = []
        for p in self._all:
            name_compact = p.name_normalized.replace(" ", "")
            score = max(
                fuzz.token_set_ratio(q, p.name_normalized),
                fuzz.partial_ratio(q_compact, name_compact)
                if len(q_compact) >= MIN_COMPACT_LEN
                else 0,
            )
            scored.append((float(score), p))
        scored.sort(key=lambda x: (-x[0], x[1].product_id))
        return scored

    @staticmethod
    def _type_tokens(p: Product) -> set[str]:
        tokens = p.name_normalized.split()
        return {t for t in tokens[1:] if t not in _GENERIC}  # 1º token = marca
