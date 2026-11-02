"""Fixtures compartilhadas."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from emporio.config import Settings
from emporio.pipeline.stages import build_pipeline

ROOT = Path(__file__).resolve().parents[1]


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
