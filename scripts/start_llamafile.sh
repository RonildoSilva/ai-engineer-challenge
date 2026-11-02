#!/usr/bin/env bash
# Sobe um modelo GGUF com o runtime oficial llamafile (Mozilla) em modo servidor
# (API compatível com OpenAI em http://HOST:PORT/v1).
#
# Uso:
#   ./scripts/start_llamafile.sh                                  # Bonsai-1.7B na porta 8080
#   MODEL=models/outro/x.gguf ALIAS=outro PORT=8081 ./scripts/start_llamafile.sh
#
# Variáveis: RUNTIME, MODEL, ALIAS, HOST, PORT, CTX, THREADS, REASONING
# Artefatos: ./scripts/download_model.sh (ver models/manifest.tsv)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${RUNTIME:-$ROOT/models/runtime/llamafile-0.10.6-thin}"
MODEL="${MODEL:-$ROOT/models/bonsai-1.7b/Bonsai-1.7B.gguf}"
ALIAS="${ALIAS:-bonsai}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
CTX="${CTX:-8192}"          # contexto suficiente p/ prompt + tools + histórico curto
THREADS="${THREADS:--1}"    # -1 = automático
REASONING="${REASONING:-off}"

for f in "$RUNTIME" "$MODEL"; do
  if [[ ! -f "$f" ]]; then
    echo "Não encontrado: $f — rode ./scripts/download_model.sh (ver models/README.md)" >&2
    exit 1
  fi
done
[[ -x "$RUNTIME" ]] || chmod +x "$RUNTIME"

echo "Subindo $(basename "$MODEL") via $(basename "$RUNTIME") como '$ALIAS' em http://$HOST:$PORT/v1 (ctx=$CTX, reasoning=$REASONING)"
# --jinja: template de chat do modelo (necessário para tool_calls estruturados)
# --reasoning: on|off|auto. A/B no Bonsai: mesmo acerto de tool calling, off é ~20% mais rápido
# --temp/--top-p/--top-k: defaults de amostragem recomendados no GGUF do Bonsai
#   (o cliente pode sobrescrever a temperatura por requisição)
exec "$RUNTIME" -m "$MODEL" --server \
  --host "$HOST" --port "$PORT" \
  --alias "$ALIAS" \
  -c "$CTX" -t "$THREADS" \
  --temp 0.5 --top-p 0.85 --top-k 20 \
  --jinja --reasoning "$REASONING"
