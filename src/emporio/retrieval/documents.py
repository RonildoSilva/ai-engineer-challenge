"""Monta os documentos de busca a partir das saídas do pipeline (store.db e policies.json).

Produtos: nome, categoria, especificações e a descrição **apenas quando confiável**
(as 7 descrições com marca divergente já chegam como NULL do pipeline).
Políticas: título + corpo curado; o texto lexical inclui as palavras-chave curadas.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from emporio.data.rules import normalize
from emporio.domain.models import Document
from emporio.domain.policies import PolicyStore


def build_documents(store_db: Path, policies_json: Path) -> list[Document]:
    docs: list[Document] = []
    con = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT p.product_id, p.name, c.name, p.specs_json, p.description, p.search_text "
            "FROM products p JOIN categories c USING (category_id) ORDER BY p.product_id"
        ).fetchall()
    finally:
        con.close()
    for pid, name, category, specs, description, search_text in rows:
        spec_txt = ", ".join(f"{k}: {v}" for k, v in json.loads(specs).items())
        text = f"{name}. Categoria: {category}. Especificações: {spec_txt}."
        if description:
            text += f" {description}"
        docs.append(Document(f"p{pid}", "product", name, text, search_text))

    for s in PolicyStore.from_json(policies_json).index():
        lexical = normalize(" ".join([s.title, *s.keywords, s.body]))
        docs.append(Document(s.id, "policy", s.title, f"{s.title}\n{s.body}", lexical))
    return docs


def text_hash(doc: Document) -> str:
    """Hash do que é embedado: se o texto não muda, o vetor em cache é reaproveitado."""
    return hashlib.sha256(f"{doc.title}\n{doc.text}".encode()).hexdigest()
