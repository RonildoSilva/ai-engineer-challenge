"""Fixtures compartilhadas."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from emporio.config import Settings
from emporio.pipeline.stages import build_pipeline

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _offline_embeddings():
    """A suíte usa o embedder determinístico (sem modelo, sem servidor, rápido).

    Escopo de sessão: fixtures de sessão (como `built_store`) são criadas antes das de função,
    então o isolamento precisa valer desde o início. Testes de integração que precisam do
    modelo real pedem isso explicitamente (`make_settings(embedding_backend="llamafile")`).
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("EMBEDDING_BACKEND", "fake")
        yield


def make_settings(**overrides) -> Settings:
    """Settings só de argumentos/ambiente (ignora o .env local do desenvolvedor)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def built_store(tmp_path_factory) -> Path:
    """Executa o pipeline real uma vez, num diretório isolado, e devolve o store.db."""
    base = tmp_path_factory.mktemp("pipeline")
    raw = base / "raw"
    shutil.copytree(ROOT / "data/raw", raw)
    settings = make_settings(data_raw_dir=raw, data_processed_dir=base / "processed")
    report = build_pipeline(settings).run()
    assert report.ok, [r for r in report.results if r.status != "ok"]
    return settings.store_db
