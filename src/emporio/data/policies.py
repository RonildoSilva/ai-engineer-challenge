"""Manual de políticas: extração do PDF, carga da curadoria e verificações entre os dois.

Fluxo:
1. `extract_sections(pdf)`: texto em modo *layout* (preserva tabelas e listas), sem cabeçalho e
   rodapé de página, dividido nas seções numeradas do manual (`capa`, `1`…`10`).
2. A curadoria (`data/curated/politicas/*.md`) é a versão revisada à mão, com front matter
   YAML e notas de curadoria para as decisões.
3. `_source.yaml` guarda o hash de cada seção do PDF usada na curadoria. Se o PDF mudar, as
   seções alteradas são apontadas e o pipeline falha até alguém revisar a curadoria.
4. `missing_facts`: todo número do PDF (prazos, valores, horários, telefones…) precisa aparecer
   na seção curada correspondente. Garante que a revisão manual não perdeu nenhum fato.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader

SOURCE_LOCK = "_source.yaml"
PAGE_NOISE = (
    re.compile(r"^\s*Empório da Música\s+Manual de Políticas e Procedimentos\s*$"),
    re.compile(r"^\s*Página \d+\s*$"),
)
# Títulos começam na coluna 0 ("4. Política…"); itens de lista numerada são indentados.
TOP_HEADING = re.compile(r"^(\d{1,2})\. (\S.*)$")
FACT = re.compile(r"\d+(?:[.,:/-]\d+)*")
CHARS_PER_TOKEN = 4  # estimativa conservadora em PT-BR (usada no orçamento de contexto)


class PolicyError(Exception):
    """Curadoria inválida, desatualizada em relação ao PDF ou incompleta."""


# ------------------------------------------------------------------------------ extração


@dataclass(frozen=True)
class SourceSection:
    ref: str  # "capa", "1" … "10"
    title: str
    text: str

    @property
    def digest(self) -> str:
        """Hash do texto com espaços normalizados (robusto a variações de layout)."""
        return hashlib.sha256(" ".join(self.text.split()).encode()).hexdigest()


def extract_text(pdf: Path) -> str:
    lines: list[str] = []
    for page in PdfReader(pdf).pages:
        for raw in page.extract_text(extraction_mode="layout").splitlines():
            line = raw.rstrip()
            if not any(p.match(line) for p in PAGE_NOISE):
                lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def split_sections(text: str) -> list[SourceSection]:
    sections: list[SourceSection] = []
    ref, title, buf = "capa", "Capa", []
    for line in text.splitlines():
        m = TOP_HEADING.match(line)
        if m:
            sections.append(SourceSection(ref, title, "\n".join(buf).strip()))
            ref, title, buf = m.group(1), m.group(2).strip(), [line]
        else:
            buf.append(line)
    sections.append(SourceSection(ref, title, "\n".join(buf).strip()))
    return sections


def extract_sections(pdf: Path) -> list[SourceSection]:
    return split_sections(extract_text(pdf))


# ------------------------------------------------------------------------------ curadoria


class FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=3)
    pdf_refs: list[str] = Field(min_length=1)
    kind: Literal["policy", "guideline"]  # guideline = instrução para o atendente/assistente
    keywords: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class CuratedSection:
    id: str
    title: str
    pdf_refs: tuple[str, ...]
    kind: str
    keywords: tuple[str, ...]
    body: str
    file: str

    @property
    def tokens_est(self) -> int:
        return math.ceil(len(self.body) / CHARS_PER_TOKEN)


def parse_curated(path: Path) -> CuratedSection:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, flags=re.DOTALL)
    if not m:
        raise PolicyError(f"{path.name}: front matter YAML ausente (bloco entre linhas ---)")
    try:
        meta = FrontMatter.model_validate(yaml.safe_load(m.group(1)))
    except (yaml.YAMLError, ValidationError) as e:
        raise PolicyError(f"{path.name}: front matter inválido: {e}") from e
    body = m.group(2).strip()
    if not body:
        raise PolicyError(f"{path.name}: seção sem conteúdo")
    return CuratedSection(
        meta.id, meta.title, tuple(meta.pdf_refs), meta.kind, tuple(meta.keywords), body, path.name
    )


def load_curated(directory: Path) -> list[CuratedSection]:
    files = sorted(p for p in directory.glob("*.md") if not p.name.startswith("_"))
    if not files:
        raise PolicyError(f"nenhuma seção curada em {directory}")
    sections = [parse_curated(p) for p in files]
    ids = [s.id for s in sections]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        raise PolicyError(f"ids de seção duplicados na curadoria: {dups}")
    return sections


# ------------------------------------------------------------------------------ verificações


def facts(text: str) -> set[str]:
    return set(FACT.findall(text))


def missing_facts(source: list[SourceSection], curated: list[CuratedSection]) -> dict[str, list]:
    """Números de cada seção do PDF ausentes nas seções curadas que a referenciam."""
    by_ref: dict[str, set[str]] = {}
    for c in curated:
        for ref in c.pdf_refs:
            by_ref.setdefault(ref, set()).update(facts(c.body))
    return {
        s.ref: sorted(facts(s.text) - by_ref.get(s.ref, set()))
        for s in source
        if facts(s.text) - by_ref.get(s.ref, set())
    }


def check(source: list[SourceSection], curated: list[CuratedSection], lock: dict) -> None:
    """Falha (PolicyError) se a curadoria não cobre, não condiz ou está velha em relação ao PDF."""
    refs = {s.ref for s in source}
    covered = {r for c in curated for r in c.pdf_refs}
    problems: list[str] = []
    if refs - covered:
        problems.append(f"seções do PDF sem curadoria: {sorted(refs - covered, key=_ref_key)}")
    if covered - refs:
        problems.append(f"curadoria cita seções inexistentes no PDF: {sorted(covered - refs)}")

    locked = lock.get("sections", {})
    changed = sorted((s.ref for s in source if locked.get(s.ref) != s.digest), key=_ref_key)
    if changed:
        files = sorted({c.file for c in curated if set(c.pdf_refs) & set(changed)})
        problems.append(
            f"o PDF mudou nas seções {changed} desde a curadoria: revise {files} e rode "
            "`emporio policies lock`"
        )
    gaps = missing_facts(source, curated)
    if gaps:
        problems.append(f"fatos do PDF ausentes na curadoria (seção: valores): {gaps}")
    if problems:
        raise PolicyError("; ".join(problems))


def _ref_key(ref: str) -> tuple[int, int]:
    return (0, 0) if ref == "capa" else (1, int(ref))


def make_lock(pdf: Path, source: list[SourceSection]) -> dict:
    return {
        "pdf": pdf.name,
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "sections": {s.ref: s.digest for s in source},
    }


def read_lock(directory: Path) -> dict:
    path = directory / SOURCE_LOCK
    if not path.exists():
        raise PolicyError(f"{path} ausente: rode `emporio policies lock` após revisar a curadoria")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_lock(directory: Path, lock: dict) -> Path:
    path = directory / SOURCE_LOCK
    header = (
        "# Hash de cada seção do PDF usada na curadoria (gerado por `emporio policies lock`).\n"
        "# Se o PDF mudar, o pipeline aponta as seções alteradas até a curadoria ser revisada.\n"
    )
    path.write_text(header + yaml.safe_dump(lock, sort_keys=False, allow_unicode=True), "utf-8")
    return path
