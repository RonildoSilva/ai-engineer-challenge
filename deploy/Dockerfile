# Imagem do Empório da Música: código + dependências travadas (uv.lock) + dados do pipeline.
# Modelos (GGUF e runtime llamafile) NÃO entram na imagem: são montados de ./models, baixados e
# verificados por ./scripts/setup.sh (ou ./scripts/download_model.sh) com SHA-256.
#
#   docker compose -f deploy/docker-compose.yml build
#
# Base fixada por digest (reprodutível); o Python é o CPython gerenciado pelo uv, na versão exata
# de .python-version, como no setup local.
FROM debian:bookworm-20251020-slim@sha256:78d2f66e0fec9e5a39fb2c72ea5e052b548df75602b5215ed01a17171529f706

ARG UV_VERSION=0.12.21
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" \
    | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) dependências (camada em cache enquanto o uv.lock não muda)
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv python install && uv sync --locked --no-dev --extra scale --no-install-project

# 2) código, configuração e dados de entrada
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY data/raw ./data/raw
COPY data/curated ./data/curated
COPY models/manifest.tsv ./models/manifest.tsv
RUN uv sync --locked --no-dev --extra scale

# 3) pipeline sem embeddings (rápido e determinístico). Os vetores são calculados no primeiro
#    `docker compose up`, pelo serviço `pipeline`, com o servidor de embeddings do compose.
RUN EMBEDDING_BACKEND=none emporio pipeline run

RUN useradd --create-home --uid 1000 emporio && chown -R emporio /app/data
USER emporio

EXPOSE 8000
CMD ["emporio", "mcp", "--http", "--host", "0.0.0.0", "--port", "8000"]
