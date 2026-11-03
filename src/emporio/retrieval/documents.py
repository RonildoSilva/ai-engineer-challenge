"""Monta os documentos de busca a partir do catálogo (Repository) e do manual (policies.json).

Produtos: nome, categoria, especificações e a descrição **apenas quando confiável**
(as 7 descrições com marca divergente já chegam como NULL do pipeline).
Políticas: título + corpo curado; o texto lexical inclui as palavras-chave curadas.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from emporio.adapters.sqlite_repo import SqliteRepository
from emporio.data.rules import normalize
from emporio.domain.models import Document
from emporio.domain.policies import PolicyStore
from emporio.ports import Repository


def build_documents(source: Repository | Path, policies_json: Path) -> list[Document]:
    """Documentos de busca. `source`: repositório (SQLite ou Postgres) ou o caminho do store.db."""
    repo = SqliteRepository(source) if isinstance(source, Path) else source
    docs: list[Document] = []
    for p in sorted(repo.list_products(), key=lambda p: p.product_id):
        spec_txt = ", ".join(f"{k}: {v}" for k, v in p.specs.items())
        text = f"{p.name}. Categoria: {p.category_name}. Especificações: {spec_txt}."
        if p.description:
            text += f" {p.description}"
        docs.append(Document(f"p{p.product_id}", "product", p.name, text, p.search_text))

    for s in PolicyStore.from_json(policies_json).index():
        lexical = normalize(" ".join([s.title, *s.keywords, s.body]))
        docs.append(Document(s.id, "policy", s.title, f"{s.title}\n{s.body}", lexical))
    return docs


def text_hash(doc: Document) -> str:
    """Hash do que é embedado: se o texto não muda, o vetor em cache é reaproveitado."""
    return hashlib.sha256(f"{doc.title}\n{doc.text}".encode()).hexdigest()
