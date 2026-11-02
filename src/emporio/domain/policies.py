"""Consulta ao manual de políticas: índice de seções e busca por assunto.

A busca é lexical e determinística: compara a pergunta normalizada com as palavras-chave
curadas de cada seção (sinônimos e formas coloquiais, como "me arrependi"), com o título e com
o corpo. Ela é o ponto de partida da detecção de assunto. A busca semântica com embeddings
entra depois, atrás de uma interface própria.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from emporio.data.rules import normalize

# Palavras que não ajudam a escolher uma seção.
STOPWORDS = frozenset({
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "um", "uma", "uns", "umas", "para", "pra", "por", "com", "sem", "que", "se", "eu", "voce",
    "vc", "meu", "minha", "meus", "minhas", "seu", "sua", "qual", "quais", "quando", "como",
    "onde", "ou", "mais", "muito", "ja", "tem", "ter", "posso", "pode", "podem", "queria",
    "quero", "gostaria", "sobre", "isso", "esse", "essa", "este", "esta", "ola", "oi", "bom",
    "dia", "tarde", "noite", "obrigado", "obrigada", "loja", "voces",
})  # fmt: skip
PREFIX = 6  # prefixo comum conta como match: "devolver" ~ "devolvo", "arrependi" ~ "arrependimento"


@dataclass(frozen=True)
class PolicySection:
    id: str
    title: str
    pdf_refs: tuple[str, ...]
    kind: str  # policy | guideline
    keywords: tuple[str, ...]
    tokens_est: int
    body: str


@dataclass(frozen=True)
class PolicyHit:
    section: PolicySection
    score: float
    matched: tuple[str, ...]


class PolicyStore:
    def __init__(self, sections: list[PolicySection]):
        self._sections = {s.id: s for s in sections}
        self._keywords = {
            s.id: tuple(dict.fromkeys(normalize(k) for k in s.keywords)) for s in sections
        }
        self._title_tokens = {s.id: _tokens(s.title) for s in sections}
        self._body_tokens = {s.id: _tokens(s.body) for s in sections}

    @classmethod
    def from_json(cls, path: Path) -> PolicyStore:
        if not path.is_file():
            raise FileNotFoundError(f"{path} não existe: rode `emporio pipeline run`")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            [
                PolicySection(
                    r["id"],
                    r["title"],
                    tuple(r["pdf_refs"]),
                    r["kind"],
                    tuple(r["keywords"]),
                    r["tokens_est"],
                    r["body"],
                )
                for r in raw
            ]
        )

    # ------------------------------------------------------------------ consulta

    def get(self, section_id: str) -> PolicySection | None:
        return self._sections.get(section_id)

    def index(self) -> list[PolicySection]:
        return list(self._sections.values())

    def search(self, query: str, k: int = 3, min_score: float = 2.0) -> list[PolicyHit]:
        """Seções mais relevantes para a pergunta, da maior para a menor pontuação.

        Pesos: palavra-chave curada = 3 (exata) ou 2 (mesmo prefixo); palavra do título = 2;
        palavra do corpo = 0,5 (até 4 palavras). Abaixo de `min_score`, nada é retornado,
        porque é melhor não pré-buscar do que pré-buscar a seção errada.
        """
        q = normalize(query)
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        hits: list[PolicyHit] = []
        for sid, section in self._sections.items():
            score = 0.0
            matched: list[str] = []
            for kw in self._keywords[sid]:
                if f" {kw} " in f" {q} ":
                    score += 3
                    matched.append(kw)
                elif (
                    " " not in kw
                    and len(kw) >= PREFIX
                    and any(len(t) >= PREFIX and t[:PREFIX] == kw[:PREFIX] for t in q_tokens)
                ):
                    score += 2
                    matched.append(kw)
            title_hits = q_tokens & self._title_tokens[sid]
            score += 2 * len(title_hits)
            score += 0.5 * min(4, len(q_tokens & self._body_tokens[sid]))
            if score >= min_score:
                matched_all = tuple(dict.fromkeys([*matched, *sorted(title_hits)]))
                hits.append(PolicyHit(section, score, matched_all))
        hits.sort(key=lambda h: (-h.score, h.section.id))
        return hits[:k]


def _tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in STOPWORDS and len(t) > 1}
