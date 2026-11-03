"""Interface web (React + Vite + Tailwind em web/): reprodutível, segura e alinhada à API.

Os testes de comportamento da interface rodam no Vitest (`npm --prefix web test`, job `web` do
CI). Aqui ficam as garantias do lado do projeto: versões exatas, nada de HTML vindo do modelo,
nenhum recurso externo, contrato com a API e o servidor `emporio web`.
"""

from __future__ import annotations

import json
import re

import pytest
from starlette.testclient import TestClient

from emporio.api.web import build_web_app
from tests.conftest import ROOT

WEB = ROOT / "web"
SRC = "\n".join(p.read_text(encoding="utf-8") for p in sorted((WEB / "src").rglob("*.ts*")))
PACKAGE = json.loads((WEB / "package.json").read_text(encoding="utf-8"))


def test_dependencies_are_pinned_and_locked():
    """Reprodutível: versões exatas no package.json, lockfile versionado, Node fixado."""
    for section in ("dependencies", "devDependencies"):
        for name, version in PACKAGE[section].items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name}@{version} não é exata"
    lock = json.loads((WEB / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] >= 3
    assert re.fullmatch(r"\d+\.\d+\.\d+", (WEB / ".nvmrc").read_text().strip())
    assert "save-exact=true" in (WEB / ".npmrc").read_text()


def test_model_text_never_becomes_html():
    """Respostas do modelo viram elementos React (parser próprio), nunca HTML."""
    assert "dangerouslySetInnerHTML" not in SRC
    assert not re.search(r"\.(innerHTML|outerHTML)\s*=", SRC)
    assert "document.write" not in SRC


def test_no_external_resources():
    """Fontes empacotadas (@fontsource), sem CDN nem Google Fonts: funciona offline."""
    index = (WEB / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"(?:src|href)=\"(https?:[^\"]+)\"", index) == []
    css = (WEB / "src/index.css").read_text(encoding="utf-8")
    assert "fonts.googleapis" not in css
    assert "@fontsource-variable/inter" in css


def test_light_and_dark_themes():
    css = (WEB / "src/index.css").read_text(encoding="utf-8")
    assert '[data-theme="dark"]' in css
    index = (WEB / "index.html").read_text(encoding="utf-8")
    assert "prefers-color-scheme: dark" in index  # tema antes do primeiro paint
    assert "localStorage" in index


def test_front_uses_the_api_contract():
    """Rotas, eventos e campos iguais aos da API REST (src/emporio/api/app.py)."""
    assert "/chat/stream" in SRC
    assert 'method: "POST"' in SRC  # a mensagem não vai na URL
    for route in ("/health", "/sessions/"):
        assert route in SRC
    for event in ("sessao", "assunto", "dados", "redigindo", "correcao", "bloqueio", "resposta",
                  "erro"):  # fmt: skip
        assert f'"{event}"' in SRC, event
    for field in ("pre_busca", "request_id", "resposta_pronta", "chamadas_llm", "prompt_tokens",
                  "latencia_s", "resposta_de_seguranca", "encaminhado"):  # fmt: skip
        assert field in SRC, field


def test_storage_is_optional():
    """Sessão e tema no navegador são conveniência: storage bloqueado não quebra a página."""
    storage = (WEB / "src/lib/storage.ts").read_text(encoding="utf-8")
    assert storage.count("try {") == 3
    uses = [ln for ln in SRC.splitlines() if "localStorage." in ln and "storage.ts" not in ln]
    assert all("window.localStorage" in ln for ln in uses)  # só dentro do storage.ts


# ============================================================================ emporio web


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "index.html").write_text("<title>Mel</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app.js").write_text("console.log(1)", encoding="utf-8")
    return tmp_path


def test_web_server_serves_the_build_and_config(dist):
    with TestClient(build_web_app(dist, "http://api.exemplo:9000/")) as web:
        assert "<title>Mel</title>" in web.get("/").text
        assert web.get("/assets/app.js").status_code == 200
        cfg = web.get("/config.js")
    assert cfg.text.strip() == 'window.EMPORIO_API = "http://api.exemplo:9000";'
    assert cfg.headers["cache-control"] == "no-store"


def test_missing_build_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"npm --prefix web ci"):
        build_web_app(tmp_path, "http://x")
