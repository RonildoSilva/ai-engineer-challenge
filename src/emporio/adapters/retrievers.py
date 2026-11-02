"""Adaptadores do `Retriever`: lexical e híbrido (lexical + vetorial, fusão RRF).

O híbrido degrada para o lexical quando o serviço de embeddings está fora do ar, quando o índice
vetorial está vazio (modelo não baixado) ou quando o índice é de outro modelo. Nunca falha por
causa da parte semântica.
"""

from __future__ import annotations

from collections.abc import Sequence

from emporio.data.rules import normalize
from emporio.domain.models import Document, SearchHit
from emporio.domain.policies import STOPWORDS, PolicyStore
from emporio.log import get_logger
from emporio.ports import Embedder, EmbedderUnavailableError

log = get_logger(__name__)
PREFIX = 6  # mesmo critério de prefixo comum da busca de políticas
POOL = 20  # candidatos de cada busca antes da fusão


def _tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in STOPWORDS and len(t) > 1}


class LexicalRetriever:
    """Busca lexical determinística, sem modelo.

    Produtos: sobreposição de palavras (exata = 1, mesmo prefixo = 0,6). Políticas:
    `PolicyStore.search` (palavras-chave curadas, título e corpo).
    """

    def __init__(self, docs: Sequence[Document], policies: PolicyStore, min_score: float = 1.0):
        self._products = [(d, set(d.lexical_text.split())) for d in docs if d.kind == "product"]
        self._docs = {d.id: d for d in docs}
        self._policies = policies
        self._min_score = min_score

    def search(self, query: str, *, k: int = 5, kind: str | None = None) -> list[SearchHit]:
        q = _tokens(query)
        if not q:
            return []
        scored: list[tuple[float, str]] = []
        if kind in (None, "product"):
            for doc, toks in self._products:
                score = 0.0
                for t in q:
                    if t in toks:
                        score += 1.0
                    elif len(t) >= PREFIX and any(x[:PREFIX] == t[:PREFIX] for x in toks):
                        score += 0.6
                if score >= self._min_score:
                    scored.append((score, doc.id))
        if kind in (None, "policy"):
            scored += [
                (h.score, h.section.id)
                for h in self._policies.search(query, k=POOL)
                if h.section.id in self._docs
            ]
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [
            SearchHit(doc_id, self._docs[doc_id].kind, self._docs[doc_id].title, score,
                      {"lexical": pos})
            for pos, (score, doc_id) in enumerate(scored[:k], start=1)
        ]  # fmt: skip


class HybridRetriever:
    """Funde o ranking lexical e o vetorial por Reciprocal Rank Fusion: score = Σ 1/(rrf_k + pos).

    Só entram resultados vetoriais com similaridade ≥ `min_similarity`; assim, uma pergunta fora
    do escopo da loja não recebe "o documento menos distante" como resposta.
    """

    def __init__(
        self,
        lexical: LexicalRetriever,
        index,  # SqliteVectorIndex (ou outro com .search/.model_id/.kind_of/.titles)
        embedder: Embedder,
        *,
        min_similarity: float = 0.35,
        rrf_k: int = 60,
    ):
        self._lexical = lexical
        self._index = index
        self._embedder = embedder
        self._min_sim = min_similarity
        self._rrf_k = rrf_k
        # permanente: não adianta tentar a parte vetorial enquanto o índice não for refeito
        self._static_reason: str | None = None
        # transitório: serviço fora do ar; tentado de novo a cada consulta
        self._transient_reason: str | None = None
        if len(index) == 0:
            self._static_reason = "índice vetorial vazio (modelo de embeddings não baixado?)"
        elif index.model_id != embedder.model_id:
            self._static_reason = (
                f"índice gerado com outro modelo ({index.model_id} ≠ {embedder.model_id}): "
                "rode `emporio pipeline run`"
            )

    @property
    def degraded_reason(self) -> str | None:
        """Por que a última busca usou só o lexical (None = busca híbrida completa)."""
        return self._static_reason or self._transient_reason

    def search(self, query: str, *, k: int = 5, kind: str | None = None) -> list[SearchHit]:
        lexical = self._lexical.search(query, k=POOL, kind=kind)
        vector = self._vector_search(query, kind)
        if vector is None:
            return lexical[:k]

        # título e tipo vêm do resultado lexical ou do índice: não presume que cubram os mesmos docs
        info = {h.doc_id: (h.kind, h.title) for h in lexical}
        fused: dict[str, float] = {}
        ranks: dict[str, dict[str, int]] = {}
        for source, ids in (("lexical", [h.doc_id for h in lexical]), ("vector", vector)):
            for pos, doc_id in enumerate(ids, start=1):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1 / (self._rrf_k + pos)
                ranks.setdefault(doc_id, {})[source] = pos
        ordered = sorted(fused, key=lambda d: (-fused[d], d))[:k]
        hits = []
        for d in ordered:
            kind_, title = info.get(d) or (self._index.kind_of(d), self._index.titles[d])
            hits.append(SearchHit(d, kind_, title, round(fused[d], 5), ranks[d]))
        return hits

    def _vector_search(self, query: str, kind: str | None) -> list[str] | None:
        if self._static_reason:
            return None
        try:
            qv = self._embedder.embed_query(query)
        except EmbedderUnavailableError as e:
            self._transient_reason = str(e)
            log.warning("busca degradada para lexical", extra={"reason": str(e)})
            return None
        self._transient_reason = None
        return [d for d, sim in self._index.search(qv, k=POOL, kind=kind) if sim >= self._min_sim]
