#!/usr/bin/env bash
# Sobe um modelo GGUF com o runtime oficial llamafile (Mozilla) em modo servidor
# (API compatível com OpenAI em http://HOST:PORT/v1).
#
# Uso:
#   ./scripts/start_llamafile.sh                      # chat: Bonsai-1.7B em :8080
#   MODE=embedding ./scripts/start_llamafile.sh       # embeddings: embeddinggemma-300m em :8081
#   MODEL=models/outro/x.gguf ALIAS=outro PORT=8082 ./scripts/start_llamafile.sh
#
# Variáveis: MODE (chat|embedding), RUNTIME, MODEL, ALIAS, HOST, PORT, CTX, THREADS, GPU,
#            REASONING (só chat)
# Artefatos: ./scripts/download_model.sh (ver models/manifest.tsv)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${MODE:-chat}"
RUNTIME="${RUNTIME:-$ROOT/models/runtime/llamafile-0.10.6-thin}"
HOST="${HOST:-127.0.0.1}"
# Núcleos físicos: no i5-8265U (4 núcleos, 8 threads) o prompt sai 35% mais rápido com 4 threads
# do que com as 8 do modo automático (19,8 × 14,6 tokens/s). Sem lscpu (macOS), automático (-1).
physical_cores() {
  command -v lscpu >/dev/null 2>&1 || { echo -1; return; }
  local n
  n="$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l)"
  [ "${n:-0}" -gt 0 ] && echo "$n" || echo -1
}
THREADS="${THREADS:-$(physical_cores)}"

case "$MODE" in
  chat)
    MODEL="${MODEL:-$ROOT/models/bonsai-1.7b/Bonsai-1.7B.gguf}"
    ALIAS="${ALIAS:-bonsai}"
    PORT="${PORT:-8080}"
    CTX="${CTX:-8192}"      # prompt + tools + histórico curto
    # CPU por padrão. Na iGPU Intel (Vulkan) desta máquina, prompts acima de algumas centenas de
    # tokens saíam corrompidos ("texto texto texto…"): erro de cálculo, não só de desempenho.
    # GPU=auto usa a GPU se existir; só use depois de validar a saída na sua placa.
    GPU="${GPU:-disable}"
    REASONING="${REASONING:-off}"
    # --jinja: template de chat do modelo (necessário para tool_calls estruturados)
    # --reasoning: A/B no Bonsai: mesmo acerto de tool calling, off é ~20% mais rápido
    # --temp/--top-p/--top-k: defaults de amostragem recomendados no GGUF do Bonsai
    MODE_ARGS=(--jinja --reasoning "$REASONING" --temp 0.5 --top-p 0.85 --top-k 20)
    ;;
  embedding)
    MODEL="${MODEL:-$ROOT/models/embeddinggemma-300m/embeddinggemma-300M-Q8_0.gguf}"
    ALIAS="${ALIAS:-embeddinggemma}"
    PORT="${PORT:-8081}"
    CTX="${CTX:-2048}"      # contexto máximo do embeddinggemma; a maior seção tem ~600 tokens
    # CPU sempre: lotes grandes de embeddings derrubaram o driver Vulkan da iGPU (DeviceLost).
    GPU="${GPU:-disable}"
    # -b/-ub = CTX: cada documento é processado num único lote (exigência de embeddings);
    # -np 1: um slot só, evita multiplicar a memória do contexto
    MODE_ARGS=(--embedding -b "$CTX" -ub "$CTX" -np 1)
    ;;
  *)
    echo "MODE inválido: $MODE (use chat ou embedding)" >&2
    exit 2
    ;;
esac

for f in "$RUNTIME" "$MODEL"; do
  if [[ ! -f "$f" ]]; then
    echo "Não encontrado: $f — rode ./scripts/download_model.sh (ver models/README.md)" >&2
    exit 1
  fi
done
[[ -x "$RUNTIME" ]] || chmod +x "$RUNTIME"

echo "Subindo $(basename "$MODEL") [$MODE] como '$ALIAS' em http://$HOST:$PORT/v1 (ctx=$CTX, gpu=$GPU, threads=$THREADS)"
# exec via bash: o llamafile é um binário APE e o bash o executa pelo sh quando o kernel recusa
exec "$RUNTIME" -m "$MODEL" --server \
  --host "$HOST" --port "$PORT" \
  --alias "$ALIAS" \
  -c "$CTX" -t "$THREADS" \
  --gpu "$GPU" \
  "${MODE_ARGS[@]}"
