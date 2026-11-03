#!/usr/bin/env bash
# Grava as conversas de exemplo (examples/*.md) com o agente real, via `emporio chat --transcript`.
# Cada conversa é uma sessão nova; o LLM e os embeddings sobem sozinhos se não estiverem no ar.
#
# Uso:  ./scripts/record_examples.sh            (perfil padrão: bonsai)
#       PROFILE=qwen3-1.7b ./scripts/record_examples.sh
#       ONLY=04 ./scripts/record_examples.sh    (só uma conversa)
#
# As respostas são as do modelo, sem edição: se ele errar, o erro fica registrado.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PROFILE="${PROFILE:-bonsai}"
OUT="${OUT:-examples}"
mkdir -p "$OUT"
# O ambiente do projeto (criado pelo setup) direto: o uv instalado via snap às vezes perde a saída
# redirecionada (exit 120); `uv run` fica como alternativa.
if [[ -x .venv/bin/emporio ]]; then EMPORIO=(.venv/bin/emporio); else EMPORIO=(uv run --locked emporio); fi

record() {  # record <id> <mensagem>...
  local id="$1"; shift
  [[ -n "${ONLY:-}" && "$ONLY" != "${id%%_*}" ]] && return 0
  echo "▶ $id ($PROFILE)"
  printf '%s\n' "$@" | LOG_LEVEL=WARNING "${EMPORIO[@]}" chat --profile "$PROFILE" \
    --transcript "$OUT/$id.md" >/dev/null
}

record 01_catalogo_ate_1000 \
  "Quais violões vocês têm até R\$1000?" \
  "e dá pra fazer o Tagima Memphis em 3x?"

record 02_endereco_horario \
  "qual o endereço da loja?" \
  "e vocês abrem no domingo?"

record 03_preco_gd20 \
  "Quanto custa o Takamine GD20?" \
  "e dá pra fazer em 10x no cartão?" \
  "tem desconto no pix?"

record 04_devolucao \
  "quero devolver o pedido 7, me arrependi da compra" \
  "leticia.rocha@jmail.com" \
  "na verdade ele veio com defeito no braço"

record 05_fora_do_escopo_acessorios \
  "vocês vendem palheta?" \
  "e corda de violão?" \
  "qual a receita de bolo de cenoura?" \
  "tá bom, então me indica um violão pra iniciante"

echo "✔ conversas em $OUT/"
