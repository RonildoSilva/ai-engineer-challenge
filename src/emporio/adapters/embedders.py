"""Adaptadores do `Embedder`.

- `OpenAICompatibleEmbedder`: qualquer servidor com `/v1/embeddings` (llamafile local, APIs online).
- `HashingEmbedder`: determinístico e sem modelo (bag-of-words com hashing), para testes e para
  rodar o pipeline sem baixar modelos. Não tem semântica: só serve de dublê.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

import openai

from emporio.data.rules import normalize
from emporio.domain.models import Document
from emporio.domain.policies import STOPWORDS
from emporio.ports import EmbedderUnavailableError


class OpenAICompatibleEmbedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        query_format: str = "{text}",
        doc_format: str = "{text}",
        timeout_s: float = 60.0,
        batch_size: int = 8,
        api_key: str = "none",
    ):
        self._client = openai.OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=2
        )
        self._model = model
        self._query_format = query_format
        self._doc_format = doc_format
        self._batch = batch_size

    @property
    def model_id(self) -> str:
        return f"{self._model}|{self._query_format}|{self._doc_format}"

    def embed_documents(self, docs: Sequence[Document]) -> list[list[float]]:
        texts = [self._doc_format.format(title=d.title, text=d.text) for d in docs]
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            out += self._embed(texts[i : i + self._batch])
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._query_format.format(text=text)])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self._client.embeddings.create(model=self._model, input=texts)
        except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as e:
            raise EmbedderUnavailableError(f"serviço de embeddings indisponível: {e}") from e
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


MIN_TOKEN_LEN = 2


class HashingEmbedder:
    """Vetor = contagem de palavras normalizadas, espalhadas em `dim` posições por hash."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    @property
    def model_id(self) -> str:
        return f"hashing-{self.dim}"

    def embed_documents(self, docs: Sequence[Document]) -> list[list[float]]:
        return [self._vec(f"{d.title} {d.text}") for d in docs]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for token in normalize(text).split():
            if token in STOPWORDS or len(token) < MIN_TOKEN_LEN:  # "de", "o"… aproximam tudo
                continue
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")
            v[h % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]
