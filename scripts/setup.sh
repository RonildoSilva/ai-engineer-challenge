#!/usr/bin/env bash
# Setup reprodutível do projeto: verifica pré-requisitos, instala dependências
# travadas (uv.lock), baixa runtime + modelo baseline com checksum, cria o .env
# e executa o pipeline de dados.
#
# Uso:  ./scripts/setup.sh            (tudo)
#       SKIP_MODEL=1 ./scripts/setup.sh   (sem baixar o modelo local)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

UV_VERSION_PINNED="0.12.21"   # mesma versão usada no desenvolvimento

ok()   { echo "✔ $*"; }
fail() { echo "✘ $*" >&2; exit 1; }

# 1. Ferramentas de sistema
for bin in bash curl sha256sum; do
  command -v "$bin" >/dev/null || fail "'$bin' não encontrado. Instale-o pelo gerenciador de pacotes do sistema."
done
ok "ferramentas de sistema: bash, curl, sha256sum"

# 2. uv (gerencia Python + dependências)
if ! command -v uv >/dev/null; then
  fail "uv não encontrado. Instale a versão fixada:
    curl -LsSf https://astral.sh/uv/${UV_VERSION_PINNED}/install.sh | sh"
fi
uv_v="$(uv --version 2>&1 | awk '{print $2}')"
[[ "$uv_v" == "$UV_VERSION_PINNED" ]] \
  && ok "uv $uv_v" \
  || echo "⚠ uv $uv_v (desenvolvido com $UV_VERSION_PINNED; versões >= 0.12 devem funcionar)"

# 3. Python (versão de .python-version, baixada pelo uv se necessário) + dependências travadas
uv python install
uv sync --locked
ok "ambiente Python: $(.venv/bin/python --version)"

# 4. Runtime llamafile (Mozilla) + modelo baseline (PrismML), ambos com SHA-256 verificado
if [[ -z "${SKIP_MODEL:-}" ]]; then
  ./scripts/download_model.sh llamafile-0.10.6
  ./scripts/download_model.sh bonsai-1.7b
else
  echo "… modelo local ignorado (SKIP_MODEL=1)"
fi

# 5. Configuração
if [[ ! -f .env ]]; then
  cp .env.example .env
  ok ".env criado a partir de .env.example"
else
  ok ".env já existe (mantido)"
fi

# 6. Pipeline de dados (incremental: só refaz etapas cujas entradas ou código mudaram)
uv run --locked emporio pipeline run
ok "pipeline de dados em dia (linhagem em data/processed/manifest.json)"

echo
echo "Pronto. Próximos passos:"
echo "  ./scripts/start_llamafile.sh                 # terminal 1"
echo "  uv run python scripts/smoke_llm.py           # terminal 2"
