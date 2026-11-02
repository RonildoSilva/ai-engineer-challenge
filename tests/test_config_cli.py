"""Testes da configuração, dos logs e da CLI `emporio`."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from emporio.cli import main
from emporio.config import PROJECT_ROOT, Settings
from emporio.log import JsonFormatter, TextFormatter
from emporio.pipeline.stages import build_pipeline

ROOT = Path(__file__).resolve().parents[1]


def settings() -> Settings:
    """Settings só de variáveis de ambiente (ignora o .env local do desenvolvedor)."""
    return Settings(_env_file=None)  # type: ignore[call-arg]  # parâmetro do pydantic-settings


# ------------------------------------------------------------------------ config


def test_defaults_resolve_relative_to_project_root():
    s = settings()
    assert s.project_root == PROJECT_ROOT == ROOT
    assert s.raw_dir == ROOT / "data/raw"
    assert s.pipeline_state_file == ROOT / "data/processed/manifest.json"
    assert s.reference_date == date(2026, 3, 25)


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("REFERENCE_DATE", "2026-04-01")
    monkeypatch.setenv("DATA_PROCESSED_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = settings()
    assert s.reference_date == date(2026, 4, 1)
    assert s.processed_dir == tmp_path
    assert s.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("var", "value"),
    [("REFERENCE_DATE", "ontem"), ("LLM_TEMPERATURE", "5"), ("LOG_FORMAT", "xml")],
)
def test_invalid_config_fails_fast(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        settings()


# ------------------------------------------------------------------------ logs


def _record(**extra) -> logging.LogRecord:
    rec = logging.makeLogRecord({"name": "emporio.x", "levelname": "INFO", "msg": "olá"})
    rec.__dict__.update(extra)
    return rec


def test_text_log_includes_extras():
    line = TextFormatter().format(_record(stage="tables", duration_s=0.4))
    assert "olá" in line and "stage=tables" in line and "duration_s=0.4" in line


def test_json_log_is_one_valid_object():
    payload = json.loads(JsonFormatter().format(_record(stage="tables")))
    assert payload["msg"] == "olá" and payload["stage"] == "tables" and payload["level"] == "INFO"


# ------------------------------------------------------------------------ pipeline do projeto


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Dados brutos copiados para um diretório temporário; saídas também isoladas."""
    raw = tmp_path / "raw"
    shutil.copytree(ROOT / "data/raw", raw)
    monkeypatch.setenv("DATA_RAW_DIR", str(raw))
    monkeypatch.setenv("DATA_PROCESSED_DIR", str(tmp_path / "processed"))
    return raw, tmp_path / "processed"


def test_project_pipeline_snapshot(isolated):
    _raw, processed = isolated
    report = build_pipeline(settings()).run()
    assert report.ok
    snap = json.loads((processed / "raw_snapshot.json").read_text())
    assert snap["products.csv"]["rows"] == 65 and snap["orders.csv"]["rows"] == 20
    assert snap["products.csv"]["columns"][0] == "product_id"
    assert len(snap["politicas_da_loja.pdf"]["sha256"]) == 64


def test_snapshot_fails_on_missing_source(isolated):
    raw, _ = isolated
    (raw / "orders.csv").unlink()
    result = build_pipeline(settings()).run().by_name()["snapshot_raw"]
    assert result.status == "failed" and "orders.csv" in (result.error or "")


# ------------------------------------------------------------------------ CLI


def test_cli_run_status_graph(isolated, capsys):
    assert main(["pipeline", "run"]) == 0
    assert "snapshot_raw" in capsys.readouterr().out
    assert main(["pipeline", "run"]) == 0
    assert "up_to_date" in capsys.readouterr().out
    assert main(["pipeline", "status"]) == 0
    assert "up_to_date" in capsys.readouterr().out
    assert main(["pipeline", "graph"]) == 0
    assert capsys.readouterr().out.startswith("flowchart LR")


def test_cli_exit_codes(isolated, monkeypatch, capsys):
    raw, _ = isolated
    assert main(["pipeline", "run", "--stage", "nada"]) == 2
    (raw / "products.csv").unlink()
    assert main(["pipeline", "run"]) == 1
    monkeypatch.setenv("REFERENCE_DATE", "ontem")
    assert main(["pipeline", "status"]) == 2
    assert "reference_date" in capsys.readouterr().err


def test_console_script_installed():
    exe = ROOT / ".venv/bin/emporio"
    if not exe.exists():
        pytest.skip("venv local não encontrado")
    result = subprocess.run([str(exe), "pipeline", "graph"], capture_output=True, text=True)
    assert result.returncode == 0 and "snapshot_raw" in result.stdout
