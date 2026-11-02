# Modelos locais

Os binários **não são versionados** (ver `.gitignore`). As fontes oficiais, fixadas por tag ou commit, e os
checksums SHA-256 ficam em [`manifest.tsv`](manifest.tsv). O script `./scripts/download_model.sh <id>`
baixa os arquivos e recusa qualquer um cujo checksum não confira. O `./scripts/setup.sh` já faz isso.

```
models/
├── manifest.tsv                         # fonte + SHA-256 de cada artefato (versionado)
├── runtime/
│   └── llamafile-0.10.6-thin            # runtime oficial Mozilla (~44 MB, CPU)
└── bonsai-1.7b/
    └── Bonsai-1.7B.gguf                 # modelo oficial PrismML (~248 MB, 1-bit Q1_0)
```

| id | Artefato | Fonte oficial |
|---|---|---|
| `llamafile-0.10.6` | runtime [llamafile](https://github.com/mozilla-ai/llamafile) v0.10.6 *thin* (sem pesos embutidos) | [release 0.10.6](https://github.com/mozilla-ai/llamafile/releases/tag/0.10.6) (digest SHA-256 publicado no GitHub) |
| `bonsai-1.7b` | [Bonsai-1.7B](https://docs.prismml.com/models/bonsai-1-7b): arquitetura Qwen3, 1.72B params, contexto de treino 32k | [prism-ml/Bonsai-1.7B-gguf](https://huggingface.co/prism-ml/Bonsai-1.7B-gguf) @ `210a9e9` |

**Por que runtime + GGUF separados (e não um `.llamafile` único)?** Os dois artefatos vêm direto dos
publicadores oficiais e têm checksum verificável. Também dá para trocar o modelo sem reempacotar nada. O
`Bonsai-1.7B.llamafile` usado no início do desenvolvimento é exatamente essa combinação: o GGUF embutido nele
tem o mesmo SHA-256 do oficial.

## Uso

```bash
./scripts/download_model.sh llamafile-0.10.6
./scripts/download_model.sh bonsai-1.7b
./scripts/start_llamafile.sh            # http://127.0.0.1:8080/v1, modelo "bonsai"
```

Testado em Linux x86_64 (8 vCPU, 16 GB RAM, sem GPU).

## Adicionando outros modelos

- **GGUF local:** adicione uma linha em `manifest.tsv` (id, destino, URL fixada em commit, SHA-256) e suba com
  `MODEL=models/<id>/<arquivo>.gguf ALIAS=<id> ./scripts/start_llamafile.sh`.
- **Outros servidores/APIs** compatíveis com OpenAI (Ollama, llama.cpp, LM Studio, APIs online): perfil em
  `config/llm_profiles.yaml` (plano, commit 8).
