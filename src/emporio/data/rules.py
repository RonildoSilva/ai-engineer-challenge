"""Regras de tratamento dos dados: as decisões de docs/data_report.md, em código testável.

Funções puras: recebem valores e devolvem valores, sem I/O.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# ------------------------------------------------------------------ disponibilidade (§2.2)

AVAILABILITY = ("disponivel", "esgotado", "descontinuado", "em_breve")


def availability(status: str, stock: int) -> str:
    """O status prevalece sobre o estoque. Só `active` com estoque > 0 é vendável."""
    if status == "discontinued":
        return "descontinuado"
    if status == "coming_soon":
        return "em_breve"
    return "disponivel" if stock > 0 else "esgotado"


# ------------------------------------------------------------------ nome × descrição (§2.3)

BRANDS = (
    "Fender", "Gibson", "Ibanez", "PRS", "Music Man", "Yamaha", "Pearl", "Tama",
    "Korg", "Nord", "Roland", "Taylor", "Martin", "Takamine", "Giannini", "Tagima",
    "Crafter", "Rozini", "Shelby", "Kala", "Ohana", "Kalani",
)  # fmt: skip
# Modelos famosos citados sem a marca na descrição.
MODEL_TO_BRAND = {"Precision": "Fender", "Jazz Bass": "Fender", "StingRay": "Music Man"}


def brands_in(text: str) -> set[str]:
    found = {b for b in BRANDS if re.search(rf"\b{re.escape(b)}\b", text)}
    found |= {brand for model, brand in MODEL_TO_BRAND.items() if model in text}
    return found


def description_is_reliable(name: str, description: str | None) -> bool:
    """Falso quando a descrição cita marca(s) e nenhuma delas é a marca do nome."""
    if not description:
        return False
    in_name, in_desc = brands_in(name), brands_in(description)
    return not (in_name and in_desc and not (in_name & in_desc))


# ------------------------------------------------------------------ texto para busca

ABBREVIATIONS: dict[str, str] = {"cedr": "cedro"}


def normalize(text: str) -> str:
    """Minúsculas, sem acentos, só letras/números, abreviações expandidas."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return " ".join(ABBREVIATIONS.get(t) or t for t in tokens)


def search_text(name: str, category: str, specs: dict[str, str], description: str | None) -> str:
    """Texto indexável do produto. A descrição só entra se for confiável."""
    parts = [name, category, *specs.values()]
    if description:
        parts.append(description)
    return normalize(" ".join(parts))


# ------------------------------------------------------------------ promoções (§3)


def current_promotion(promos: Iterable[tuple[int, int, str]]) -> tuple[int, int, str] | None:
    """Entre as promoções ATIVAS (id, %, rótulo) de um produto, vale a de maior desconto.

    Promoções não são cumulativas (manual §6.2). Em empate, vale o menor id (a mais antiga).
    """
    promos = list(promos)
    if not promos:
        return None
    return min(promos, key=lambda p: (-p[1], p[0]))


def promo_price(price: float, percent: int) -> float:
    return round(price * (1 - percent / 100), 2)


# ------------------------------------------------------------------ pedidos e clientes


def parse_payment(method: str) -> tuple[str, int]:
    """`credit_6x` → ("credito", 6); `pix` → ("pix", 1)."""
    if method.startswith("credit_"):
        return "credito", int(method.removeprefix("credit_").removesuffix("x"))
    return {"pix": "pix", "debit": "debito", "boleto": "boleto"}[method], 1


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def first_name(name: str) -> str:
    return name.split(maxsplit=1)[0]


METRO_CITIES = {"campo grande"}  # região metropolitana de Campo Grande (manual §5.1)


def is_metro(city: str) -> bool:
    return normalize(city) in METRO_CITIES
