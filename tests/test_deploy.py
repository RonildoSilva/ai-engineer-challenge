"""Configuração de deploy: imagens fixadas, nada privado na imagem, versões iguais às do setup."""

from __future__ import annotations

import re

import yaml

from tests.conftest import ROOT

DOCKERFILE = (ROOT / "deploy/Dockerfile").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8"))
CI = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def test_dockerfile_base_is_pinned_by_digest():
    bases = re.findall(r"^FROM\s+(\S+)", DOCKERFILE, flags=re.MULTILINE)
    assert bases
    assert all(DIGEST.search(b) for b in bases), bases


def test_dockerfile_uses_the_same_uv_as_setup():
    setup = (ROOT / "scripts/setup.sh").read_text(encoding="utf-8")
    pinned = re.search(r'UV_VERSION_PINNED="([\d.]+)"', setup)
    assert pinned
    assert f"ARG UV_VERSION={pinned.group(1)}" in DOCKERFILE
    assert "uv sync --locked" in DOCKERFILE  # versões exatas do uv.lock


def test_dockerignore_is_an_allowlist_without_private_files():
    lines = [ln.strip() for ln in (ROOT / ".dockerignore").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]  # fmt: skip
    assert lines[0] == "*"  # tudo fora por padrão
    allowed = {ln[1:].rstrip("/") for ln in lines if ln.startswith("!")}
    assert not allowed & {"_raw", ".env", ".venv", "models", "data/processed", "data"}
    assert {"src", "uv.lock", "data/raw", "data/curated"} <= allowed


def test_compose_third_party_images_are_pinned_and_app_is_built():
    for name, svc in COMPOSE["services"].items():
        if "image" in svc and "build" not in svc:
            assert DIGEST.search(svc["image"]), name
    assert COMPOSE["services"]["mcp"]["build"]["dockerfile"] == "deploy/Dockerfile"


def test_compose_profiles():
    services = COMPOSE["services"]
    default = {n for n, s in services.items() if not s.get("profiles")}
    assert {"llm", "embeddings", "pipeline", "mcp"} <= default
    assert {n for n, s in services.items() if "scale" in s.get("profiles", [])} == {
        "postgres", "db-load", "mcp-scale",
    }  # fmt: skip
    scale = services["mcp-scale"]["environment"]
    assert (scale["STORE_BACKEND"], scale["VECTOR_BACKEND"]) == ("postgres", "pgvector")
    # modelos montados somente leitura, nunca copiados para a imagem
    assert "../models:/app/models:ro" in services["llm"]["volumes"]


def test_ci_postgres_job_uses_the_same_image_as_compose():
    ci_image = CI["jobs"]["postgres"]["services"]["postgres"]["image"]
    assert ci_image == COMPOSE["services"]["postgres"]["image"]
    assert "tests/test_postgres.py" in str(CI["jobs"]["postgres"]["steps"])
