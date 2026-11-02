#!/usr/bin/env bash
# Baixa um modelo listado em models/manifest.tsv e verifica o SHA-256.
#
# Uso:  ./scripts/download_model.sh [id]        (default: bonsai-1.7b)
#       FORCE=1 ./scripts/download_model.sh     (rebaixa mesmo se já existir)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ID="${1:-bonsai-1.7b}"
MANIFEST="$ROOT/models/manifest.tsv"

line="$(grep -v '^#' "$MANIFEST" | awk -F'\t' -v id="$ID" '$1 == id')"
if [[ -z "$line" ]]; then
  echo "Modelo '$ID' não encontrado em $MANIFEST" >&2
  exit 1
fi
IFS=$'\t' read -r _ dest url sha <<<"$line"
dest="$ROOT/$dest"

verify() { echo "$sha  $1" | sha256sum --check --status; }

if [[ -f "$dest" && -z "${FORCE:-}" ]]; then
  if verify "$dest"; then
    echo "✔ $ID já presente e íntegro: $dest"
    exit 0
  fi
  echo "✘ $dest existe mas o SHA-256 não confere com o manifesto." >&2
  echo "  Use FORCE=1 para substituir pela versão fixada." >&2
  exit 2
fi

mkdir -p "$(dirname "$dest")"
tmp="$dest.part"
echo "↓ Baixando $ID de $url"
curl -fL --retry 3 --progress-bar -o "$tmp" "$url"

if ! verify "$tmp"; then
  echo "✘ SHA-256 inválido para o download de $ID (esperado $sha)." >&2
  rm -f "$tmp"
  exit 3
fi
mv "$tmp" "$dest"
chmod +x "$dest"
echo "✔ $ID salvo em $dest"
