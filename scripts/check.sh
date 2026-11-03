#!/usr/bin/env bash
# Verificação completa do projeto (mesma sequência do CI).
#
# Uso:  ./scripts/check.sh          (lint + lock + testes unitários + integração se houver modelo)
#       FAST=1 ./scripts/check.sh   (sem integração)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { echo; echo "▶ $*"; }

step "uv.lock em dia com pyproject.toml"
uv lock --check

step "lint e formatação (ruff)"
uv run --locked ruff check .
uv run --locked ruff format --check .

step "testes unitários"
uv run --locked pytest

if [[ -d web/node_modules ]]; then
  step "interface web: tipos, testes e build (versões do package-lock.json)"
  npm --prefix web run typecheck
  npm --prefix web test
  npm --prefix web run build >/dev/null
else
  echo "… interface web: dependências não instaladas (o setup instala quando há Node)"
fi

if [[ -z "${FAST:-}" ]]; then
  step "testes de integração (LLM local real)"
  uv run --locked pytest -m integration -rs
fi

echo; echo "✔ tudo verde"
