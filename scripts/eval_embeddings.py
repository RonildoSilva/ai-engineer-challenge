"""Avaliação de viabilidade: modelos de embeddings × busca lexical × híbrida (RRF).

Para cada modelo candidato (GGUF), sobe o runtime llamafile em modo `--embedding`, indexa os
65 produtos e as 10 seções de políticas e mede, em 20 perguntas com gabarito:
hit@1, hit@3, MRR, latência por consulta, tempo de indexação, memória (RSS) e dimensão.

Uso:
    uv run python scripts/eval_embeddings.py [--models a,b] [--out arquivo.json]

Requer o pipeline já executado (store.db e policies.json). O modelo escolhido vem do manifesto;
os demais candidatos são baixados à parte (comandos em docs/embeddings_eval.md).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from emporio.config import Settings
from emporio.data.rules import normalize
from emporio.domain.policies import STOPWORDS, PolicyStore

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "models/runtime/llamafile-0.10.6-thin"
QWEN_TASK = (
    "Given a customer question to a musical instrument store, "
    "retrieve the passage or product that answers it"
)


@dataclass(frozen=True)
class Candidate:
    id: str
    gguf: str
    query_fmt: str = "{q}"
    doc_fmt: str = "{text}"


CANDIDATES = {
    c.id: c
    for c in [
        Candidate(
            "qwen3-emb-0.6b",
            "models/candidates/qwen3-emb-0.6b/Qwen3-Embedding-0.6B-Q8_0.gguf",
            query_fmt=f"Instruct: {QWEN_TASK}\nQuery: {{q}}",
        ),
        Candidate(
            "embeddinggemma-300m",
            "models/embeddinggemma-300m/embeddinggemma-300M-Q8_0.gguf",  # escolhido
            query_fmt="task: search result | query: {q}",
            doc_fmt="title: {title} | text: {text}",
        ),
        Candidate(
            "nomic-v2-moe",
            "models/candidates/nomic-v2-moe/nomic-embed-text-v2-moe.Q8_0.gguf",
            query_fmt="search_query: {q}",
            doc_fmt="search_document: {text}",
        ),
        Candidate("bge-m3", "models/candidates/bge-m3/bge-m3-q8_0.gguf"),
    ]
}

# (pergunta, ids aceitos). Produtos: "p<id>"; políticas: id da seção.
QUERIES: list[tuple[str, set[str]]] = [
    # produtos (perguntas vagas, sem nome de modelo)
    ("um violão bom e barato pra quem está começando", {"p81", "p83", "p85"}),
    ("ukulele pequeno para uma criança", {"p116", "p117", "p118", "p119"}),
    ("violão de sete cordas para tocar chorinho", {"p109", "p110", "p111", "p112", "p113"}),
    ("violão que dá pra ligar na caixa de som, com cordas de aço",
     {"p103", "p104", "p105", "p106", "p107", "p108"}),
    ("guitarra para tocar heavy metal rápido", {"p132"}),
    ("baixo elétrico de quatro cordas", {"p135", "p136", "p137", "p138"}),
    ("bateria acústica completa para estúdio", {"p139", "p140", "p141"}),
    ("teclado sintetizador para produzir música", {"p142", "p143", "p144"}),
    ("guitarra com timbre encorpado para blues", {"p131", "p134", "p145"}),
    ("violão clássico de nylon de nível intermediário", {"p82", "p114"}),
    # políticas (pouca sobreposição de palavras com o manual)
    ("comprei, me arrependi e quero meu dinheiro de volta", {"trocas_devolucoes"}),
    ("vocês funcionam no fim de semana?", {"horarios"}),
    ("posso pagar metade no pix e metade no cartão?", {"pagamento"}),
    ("em quanto tempo uma encomenda chega em Corumbá?", {"frete_entregas"}),
    ("o instrumento parou de funcionar depois de quatro meses de uso", {"garantia"}),
    ("vai ter liquidação em novembro?", {"promocoes"}),
    ("como faço pra vocês esquecerem meu cadastro?", {"privacidade"}),
    ("onde vocês ficam?", {"sobre_contato"}),
    ("estou muito chateado com o atendimento, quero reclamar", {"atendimento"}),
    ("o pacote chegou amassado, o que eu faço?", {"frete_entregas", "trocas_devolucoes"}),
]  # fmt: skip


# ------------------------------------------------------------------------------ corpus


def load_corpus(settings: Settings) -> list[dict]:
    docs: list[dict] = []
    con = sqlite3.connect(f"file:{settings.store_db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT p.product_id, p.name, c.name, p.specs_json, p.description, p.search_text "
        "FROM products p JOIN categories c USING (category_id)"
    ).fetchall()
    con.close()
    for pid, name, cat, specs, desc, search in rows:
        spec_txt = ", ".join(f"{k}: {v}" for k, v in json.loads(specs).items())
        text = f"{name}. Categoria: {cat}. Especificações: {spec_txt}."
        if desc:  # descrições não confiáveis já chegam como NULL do pipeline
            text += f" {desc}"
        docs.append({"id": f"p{pid}", "title": name, "text": text, "lexical": search})
    docs.extend(
        {"id": s.id, "title": s.title, "text": f"{s.title}\n{s.body}", "policy": s}
        for s in PolicyStore.from_json(settings.policies_json).index()
    )
    return docs


# ------------------------------------------------------------------------------ rankings


def lexical_rank(query: str, docs: list[dict], store: PolicyStore) -> list[str]:
    """Baseline sem embeddings: PolicyStore.search para políticas, sobreposição para produtos."""
    q = {t for t in normalize(query).split() if t not in STOPWORDS and len(t) > 1}
    scored: list[tuple[float, str]] = []
    for d in docs:
        if "lexical" in d:
            toks = set(d["lexical"].split())
            score = sum(1.0 if t in toks else 0.6 for t in q if any(x[:6] == t[:6] for x in toks))
            scored.append((score, d["id"]))
    scored += [(h.score, h.section.id) for h in store.search(query, k=10, min_score=0.5)]
    scored.sort(key=lambda x: -x[0])
    return [i for s, i in scored if s > 0]


def rrf(*rankings: list[str], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for pos, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1 / (k + pos + 1)
    return sorted(scores, key=lambda d: -scores[d])


def metrics(rankings: list[list[str]]) -> dict[str, float]:
    h1 = h3 = mrr = 0.0
    for ranking, (_, gold) in zip(rankings, QUERIES, strict=True):
        pos = next((i for i, d in enumerate(ranking) if d in gold), None)
        h1 += pos == 0
        h3 += pos is not None and pos < 3
        mrr += 1 / (pos + 1) if pos is not None else 0
    n = len(QUERIES)
    return {"hit@1": round(h1 / n, 2), "hit@3": round(h3 / n, 2), "mrr": round(mrr / n, 3)}


# ------------------------------------------------------------------------------ servidor


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(  # noqa: S310 — servidor local
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
            return json.load(r)
    except urllib.error.HTTPError as e:  # mostra a causa devolvida pelo servidor
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}") from e


def rss_mb(pid: int) -> float:
    """RSS somado do processo e filhos (o llamafile pode reexecutar a si mesmo)."""
    out = subprocess.run(  # noqa: S603
        ["ps", "-o", "rss=", "--ppid", str(pid), "-p", str(pid)],  # noqa: S607
        capture_output=True, text=True, check=False,
    ).stdout  # fmt: skip
    return round(sum(int(x) for x in out.split()) / 1024, 0)


def embed_all(base: str, texts: list[str], batch: int = 16) -> list[list[float]]:
    vecs: list[list[float]] = []
    for i in range(0, len(texts), batch):
        data = post(f"{base}/v1/embeddings", {"input": texts[i : i + batch], "model": "emb"})
        vecs += [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
    return [_unit(v) for v in vecs]


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def evaluate(cand: Candidate, docs: list[dict], lexical: list[list[str]]) -> dict:
    port = free_port()
    t0 = time.perf_counter()
    proc = subprocess.Popen(  # noqa: S603
        # llamafile é um binário APE: o kernel não o executa direto via exec(); o sh, sim.
        # --gpu disable: CPU em qualquer máquina (lotes grandes derrubavam o driver da iGPU).
        # Configuração da avaliação (4 slots, contexto 8192) é mais pesada que a de produção.
        ["/bin/sh", str(RUNTIME), "-m", str(ROOT / cand.gguf), "--server", "--embedding",
         "--gpu", "disable", "--host", "127.0.0.1", "--port", str(port), "--alias", "emb",
         "-c", "8192", "-b", "8192", "-ub", "8192"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )  # fmt: skip
    base = f"http://127.0.0.1:{port}"
    try:
        while True:
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=2) as r:  # noqa: S310
                    if json.load(r).get("status") == "ok":
                        break
            except OSError:
                if proc.poll() is not None:
                    return {"error": f"servidor encerrou ({proc.returncode})"}
                time.sleep(0.5)
        load_s = time.perf_counter() - t0

        t1 = time.perf_counter()
        doc_vecs = embed_all(
            base, [cand.doc_fmt.format(title=d["title"], text=d["text"]) for d in docs]
        )
        index_s = time.perf_counter() - t1

        t2 = time.perf_counter()
        q_vecs = embed_all(base, [cand.query_fmt.format(q=q) for q, _ in QUERIES], batch=1)
        query_ms = (time.perf_counter() - t2) / len(QUERIES) * 1000
        mem = rss_mb(proc.pid)
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)

    dense = [
        [docs[i]["id"] for i in sorted(range(len(docs)), key=lambda i: -_dot(qv, doc_vecs[i]))]
        for qv in q_vecs
    ]
    hybrid = [rrf(d, lx) for d, lx in zip(dense, lexical, strict=True)]
    return {
        "dim": len(doc_vecs[0]),
        "load_s": round(load_s, 1),
        "index_s": round(index_s, 1),
        "query_ms": round(query_ms),
        "rss_mb": mem,
        "dense": metrics(dense),
        "hybrid": metrics(hybrid),
        "misses_dense": [
            q for (q, gold), r in zip(QUERIES, dense, strict=True) if r[0] not in gold
        ],
        "top1_dense": [r[0] for r in dense],
    }


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(CANDIDATES))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    settings = Settings()
    docs = load_corpus(settings)
    store = PolicyStore.from_json(settings.policies_json)
    lexical = [lexical_rank(q, docs, store) for q, _ in QUERIES]
    results = {"lexical": {**metrics(lexical), "top1": [r[0] if r else None for r in lexical]}}
    print(f"corpus: {len(docs)} documentos · {len(QUERIES)} perguntas")
    print(f"{'lexical (atual)':<22} {results['lexical']}")
    for mid in args.models.split(","):
        try:
            r = evaluate(CANDIDATES[mid], docs, lexical)
        except (RuntimeError, OSError) as e:  # um modelo inviável não interrompe os outros
            r = {"error": str(e)}
        results[mid] = r
        summary = {k: v for k, v in r.items() if k != "top1_dense"}
        print(f"{mid:<22} {json.dumps(summary, ensure_ascii=False)}")
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
