"""Testes da infraestrutura do commit 1: versões fixadas, manifesto, scripts e .gitignore.

Rápidos e offline (não baixam nada da internet nem sobem o modelo).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False, **kw)


def manifest_rows() -> list[list[str]]:
    lines = (ROOT / "models" / "manifest.tsv").read_text().splitlines()
    return [ln.split("\t") for ln in lines if ln.strip() and not ln.startswith("#")]


# --------------------------------------------------------------------- versões fixadas


def test_python_version_pinned_and_consistent():
    pinned = (ROOT / ".python-version").read_text().strip()
    assert re.fullmatch(r"3\.12\.\d+", pinned), "Python deve estar fixado em versão exata"
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["requires-python"] == ">=3.12,<3.13"
    assert pyproject["tool"]["uv"]["python-preference"] == "only-managed"


def test_running_interpreter_matches_pin():
    import sys

    pinned = (ROOT / ".python-version").read_text().strip()
    assert ".".join(map(str, sys.version_info[:3])) == pinned


def test_uv_version_pinned_in_setup_and_readme():
    setup = (SCRIPTS / "setup.sh").read_text()
    match = re.search(r'UV_VERSION_PINNED="([\d.]+)"', setup)
    assert match, "setup.sh deve fixar UV_VERSION_PINNED"
    pinned = match.group(1)
    assert f"astral.sh/uv/{pinned}/install.sh" in (ROOT / "README.md").read_text()


def test_lockfile_exists():
    assert (ROOT / "uv.lock").is_file()


# --------------------------------------------------------------------- manifesto


@pytest.mark.parametrize("row", manifest_rows(), ids=lambda r: r[0])
def test_manifest_row_is_pinned(row):
    assert len(row) == 4, "id, destino, url, sha256"
    _id, dest, url, sha = row
    assert re.fullmatch(r"[0-9a-f]{64}", sha), "SHA-256 inválido"
    assert dest.startswith("models/")
    assert not dest.startswith("models/manifest")
    assert url.startswith("https://")
    # URL fixada: release com tag de versão ou commit de 40 hex (nunca 'main'/'latest')
    assert re.search(r"/download/\d+\.\d+\.\d+/|/resolve/[0-9a-f]{40}/", url), url


def test_manifest_has_runtime_and_baseline():
    ids = {r[0] for r in manifest_rows()}
    assert {"llamafile-0.10.6", "bonsai-1.7b", "embeddinggemma-300m"} <= ids


def test_setup_downloads_every_manifest_artifact():
    setup = (SCRIPTS / "setup.sh").read_text()
    for row in manifest_rows():
        assert f"download_model.sh {row[0]}" in setup, f"setup.sh não baixa {row[0]}"


@pytest.mark.parametrize("row", manifest_rows(), ids=lambda r: r[0])
def test_downloaded_artifacts_match_manifest(row):
    """Se o artefato já foi baixado, ele precisa bater com o checksum."""
    _id, dest, _url, sha = row
    path = ROOT / dest
    if not path.exists():
        pytest.skip(f"{dest} não baixado (rode ./scripts/download_model.sh {_id})")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    assert h.hexdigest() == sha


# --------------------------------------------------------------------- download_model.sh


@pytest.fixture
def fake_artifact(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"conteudo de teste")
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    dest = tmp_path / "out" / "artefato.bin"

    def manifest(checksum: str = sha) -> Path:
        m = tmp_path / "manifest.tsv"
        m.write_text(f"# comentario\nfake\t{dest}\tfile://{src}\t{checksum}\n")
        return m

    return dest, manifest


def download(manifest: Path, id_: str = "fake", **env) -> subprocess.CompletedProcess[str]:
    import os

    return run(
        [str(SCRIPTS / "download_model.sh"), id_],
        env={**os.environ, "MANIFEST": str(manifest), **env},
    )


def test_download_ok_and_idempotent(fake_artifact):
    dest, manifest = fake_artifact
    m = manifest()
    first = download(m)
    assert first.returncode == 0, first.stderr
    assert dest.read_bytes() == b"conteudo de teste"
    second = download(m)
    assert second.returncode == 0
    assert "já presente e íntegro" in second.stdout


def test_download_rejects_bad_checksum(fake_artifact):
    dest, manifest = fake_artifact
    result = download(manifest("0" * 64))
    assert result.returncode == 3
    assert not dest.exists()
    assert not dest.with_suffix(".bin.part").exists()


def test_download_refuses_corrupted_existing_file(fake_artifact):
    dest, manifest = fake_artifact
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"corrompido")
    result = download(manifest())
    assert result.returncode == 2
    assert "FORCE=1" in result.stderr
    assert download(manifest(), FORCE="1").returncode == 0
    assert dest.read_bytes() == b"conteudo de teste"


def test_download_unknown_id(fake_artifact):
    _dest, manifest = fake_artifact
    assert download(manifest(), "nao-existe").returncode == 1


# --------------------------------------------------------------------- start_llamafile.sh


@pytest.mark.parametrize("mode", ["chat", "embedding"])
def test_start_fails_clearly_without_artifacts(mode):
    import os

    result = run(
        [str(SCRIPTS / "start_llamafile.sh")],
        env={**os.environ, "MODE": mode, "RUNTIME": "/nao/existe", "MODEL": "/nao/existe.gguf"},
        timeout=10,
    )
    assert result.returncode == 1
    assert "download_model.sh" in result.stderr


def test_start_rejects_invalid_mode():
    import os

    result = run([str(SCRIPTS / "start_llamafile.sh")], env={**os.environ, "MODE": "x"}, timeout=10)
    assert result.returncode == 2
    assert "MODE inválido" in result.stderr


def test_start_defaults_gpu_policy():
    """CPU por padrão: a iGPU (Vulkan) corrompia prompts longos e derrubava o driver."""
    script = (SCRIPTS / "start_llamafile.sh").read_text()
    chat, embedding = script.split("  embedding)")
    assert 'GPU="${GPU:-disable}"' in chat
    assert 'GPU="${GPU:-disable}"' in embedding


@pytest.mark.parametrize("script", sorted(p.name for p in SCRIPTS.glob("*.sh")))
def test_shell_scripts_are_valid_and_executable(script):
    path = SCRIPTS / script
    assert path.stat().st_mode & 0o111, f"{script} sem permissão de execução"
    assert run(["bash", "-n", str(path)]).returncode == 0, "erro de sintaxe bash"


# --------------------------------------------------------------------- .gitignore


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("models/runtime/llamafile-0.10.6-thin", True),
        ("models/bonsai-1.7b/Bonsai-1.7B.gguf", True),
        ("models/manifest.tsv", False),
        ("models/README.md", False),
        (".env", True),
        (".env.example", False),
        ("_raw/qualquer.md", True),
        ("data/processed/store.db", True),
        ("data/raw/products.csv", False),
    ],
)
def test_gitignore(path, ignored):
    result = run(["git", "check-ignore", "-q", "--no-index", path])
    assert (result.returncode == 0) is ignored
