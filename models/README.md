# Modelos locais

Os binários **não são versionados** (ver `.gitignore`). As fontes oficiais, fixadas por tag ou commit, e os
checksums SHA-256 ficam em [`manifest.tsv`](manifest.tsv). O script `./scripts/download_model.sh <id>`
baixa os arquivos e recusa qualquer um cujo checksum não confira. O `./scripts/setup.sh` já baixa todos (há
um teste garantindo que todo artefato do manifesto está no setup).

```
models/
├── manifest.tsv                              # fonte + SHA-256 de cada artefato (versionado)
├── runtime/
│   └── llamafile-0.10.6-thin                 # runtime oficial Mozilla (~44 MB)
├── bonsai-1.7b/
│   └── Bonsai-1.7B.gguf                      # LLM de chat, oficial PrismML (~248 MB, 1-bit Q1_0)
└── embeddinggemma-300m/
    └── embeddinggemma-300M-Q8_0.gguf         # embeddings, Google via ggml-org (~334 MB, Q8_0)
```

| id | Artefato | Fonte oficial |
|---|---|---|
| `llamafile-0.10.6` | runtime [llamafile](https://github.com/mozilla-ai/llamafile) v0.10.6 *thin* (sem pesos embutidos) | [release 0.10.6](https://github.com/mozilla-ai/llamafile/releases/tag/0.10.6) (digest SHA-256 publicado no GitHub) |
| `bonsai-1.7b` | [Bonsai-1.7B](https://docs.prismml.com/models/bonsai-1-7b): arquitetura Qwen3, 1.72B params, contexto de treino 32k | [prism-ml/Bonsai-1.7B-gguf](https://huggingface.co/prism-ml/Bonsai-1.7B-gguf) @ `210a9e9` |
| `embeddinggemma-300m` | EmbeddingGemma 300M (Google): multilíngue, 768 dimensões, contexto de 2048 tokens. Licença *Gemma Terms of Use* | [ggml-org/embeddinggemma-300M-GGUF](https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF) @ `0f741b5` |

**Por que runtime + GGUF separados (e não um `.llamafile` único)?** Os artefatos vêm direto dos
publicadores oficiais e têm checksum verificável. O mesmo runtime serve o LLM e os embeddings, e trocar um
modelo não exige reempacotar nada. O `Bonsai-1.7B.llamafile` usado no início do desenvolvimento é exatamente
a combinação runtime + GGUF: o GGUF embutido nele tem o mesmo SHA-256 do oficial.

**Por que o embeddinggemma?** Ele venceu a avaliação com 4 candidatos (90% de acerto no 1º resultado contra
70% da busca lexical, 104 ms por consulta em CPU, o menor download). Detalhes e como reproduzir:
[`docs/embeddings_eval.md`](../docs/embeddings_eval.md).

## Uso

```bash
./scripts/download_model.sh llamafile-0.10.6
./scripts/download_model.sh bonsai-1.7b
./scripts/download_model.sh embeddinggemma-300m
./scripts/start_llamafile.sh                   # chat:       http://127.0.0.1:8080/v1, modelo "bonsai"
MODE=embedding ./scripts/start_llamafile.sh    # embeddings: http://127.0.0.1:8081/v1, modelo "embeddinggemma"
```

## CPU × GPU

O llamafile usa GPU automaticamente quando encontra uma. Testado em Linux x86_64 com 8 vCPU, 16 GB de RAM,
Intel UHD 620 e NVIDIA MX150.

| Modo | Padrão | Motivo |
|---|---|---|
| `chat` | `GPU=disable` (CPU) | Na iGPU (Vulkan) desta máquina, prompts curtos saíam certos (~5 s contra ~18 s em CPU), mas o prompt real do agente (~2 mil tokens) saía **corrompido** ("texto texto texto…"). Erro de cálculo, não de desempenho. |
| `embedding` | `GPU=disable` (CPU) | Lotes grandes de embeddings derrubaram o driver Vulkan da iGPU (`vk::DeviceLostError`). Em CPU, uma consulta leva ~60 ms e o servidor usa ~185 MB de RAM. |

Ambos aceitam `GPU=disable|auto|nvidia|amd|apple`. Use GPU só depois de validar a saída na sua placa com um
prompt longo (por exemplo, `uv run emporio ask "Quanto custa o Takamine GD20?" --debug`).

## Adicionando outros modelos

- **GGUF local:** adicione uma linha em `manifest.tsv` (id, destino, URL fixada em commit, SHA-256), inclua o
  download no `setup.sh` e suba com
  `MODEL=models/<id>/<arquivo>.gguf ALIAS=<id> [MODE=embedding] ./scripts/start_llamafile.sh`.
- **Outros servidores/APIs** compatíveis com OpenAI (Ollama, llama.cpp, LM Studio, APIs online): perfil em
  `config/llm_profiles.yaml` (ver `uv run emporio llm list`).
