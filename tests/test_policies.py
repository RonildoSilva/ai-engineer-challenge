"""Manual de políticas: extração do PDF, curadoria verificada, busca por assunto e CLI."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from emporio.cli import main
from emporio.data import policies as pol
from emporio.domain.policies import PolicyStore
from emporio.pipeline.stages import build_pipeline
from tests.conftest import ROOT, make_settings

PDF = ROOT / "data/raw/politicas_da_loja.pdf"
CURATED = ROOT / "data/curated/politicas"


@pytest.fixture(scope="module")
def source() -> list[pol.SourceSection]:
    return pol.extract_sections(PDF)


@pytest.fixture
def store(built_store) -> PolicyStore:
    return PolicyStore.from_json(built_store.parent / "policies.json")


# ============================================================================ extração do PDF


def test_sections_follow_manual_numbering(source):
    assert [s.ref for s in source] == ["capa", *map(str, range(1, 11))]
    titles = {s.ref: s.title for s in source}
    assert titles["4"] == "Política de Trocas e Devoluções"
    assert titles["7"] == "Atendimento via WhatsApp"


def test_extraction_is_clean(source):
    text = "\n".join(s.text for s in source)
    assert "Página" not in text  # rodapés removidos
    assert "Manual de Políticas e Procedimentos\n" not in text.split("1. Sobre")[1]  # cabeçalhos
    assert "(67) 3341-4444" in text  # números não quebram no modo layout
    assert "R$ 2.000,00" in text


def test_indented_numbered_list_is_not_a_section(source):
    sec7 = next(s for s in source if s.ref == "7")
    assert "Saudação" in sec7.text
    assert "Fechamento" in sec7.text


def test_split_sections_synthetic():
    text = "Capa\n\n1. Primeira\nconteúdo\n    2.   item de lista\n2. Segunda\nfim"
    sections = pol.split_sections(text)
    assert [(s.ref, s.title) for s in sections] == [
        ("capa", "Capa"),
        ("1", "Primeira"),
        ("2", "Segunda"),
    ]
    assert "item de lista" in sections[1].text


def test_digest_ignores_layout_whitespace():
    a = pol.SourceSection("1", "t", "Frete   grátis\n  acima de R$ 500,00")
    b = pol.SourceSection("1", "t", "Frete grátis acima de R$ 500,00")
    assert a.digest == b.digest
    assert a.digest != pol.SourceSection("1", "t", "Frete grátis acima de R$ 600,00").digest


def test_facts():
    assert pol.facts("até 12x, R$ 2.000,00, 09:00 às 18:00, (67) 3341-4444") == {
        "12",
        "2.000,00",
        "09:00",
        "18:00",
        "67",
        "3341-4444",
    }


# ============================================================================ curadoria real


def test_real_curation_is_complete_and_current(source):
    curated = pol.load_curated(CURATED)
    pol.check(source, curated, pol.read_lock(CURATED))  # não levanta
    assert len(curated) == 10
    assert {c.kind for c in curated} == {"policy", "guideline"}


def test_curated_decisions(store):
    contato = store.get("sobre_contato").body
    assert "(67) 3341-4444" in contato
    assert "(67) 3321-4500" in contato  # os dois telefones, cada um com seu papel
    assert "| E-mail | contato@emporiodamusica.com.br |" in contato  # sem acento
    pagamento = store.get("pagamento").body
    assert "De 4x a 6x:** sem juros, parcela mínima de R$ 80,00" in pagamento  # §3.1 prevalece


def test_published_json(built_store):
    data = json.loads((built_store.parent / "policies.json").read_text())
    assert [d["id"] for d in data][:2] == ["sobre_contato", "horarios"]
    assert all(d["tokens_est"] > 0 for d in data)
    assert sum(d["tokens_est"] for d in data) < 4000  # o manual cabe em contextos pequenos


# ============================================================================ verificações


@pytest.fixture
def curated_copy(tmp_path) -> Path:
    root = tmp_path / "curated"
    shutil.copytree(CURATED, root / "politicas")
    return root / "politicas"


def _check(curated_dir: Path, source) -> None:
    pol.check(source, pol.load_curated(curated_dir), pol.read_lock(curated_dir))


def test_missing_fact_is_detected(curated_copy, source):
    f = curated_copy / "05_frete_entregas.md"
    f.write_text(f.read_text().replace("taxa fixa de R$ 35,00", "taxa fixa"))
    with pytest.raises(pol.PolicyError, match=r"'5': \['35,00'\]"):
        _check(curated_copy, source)


def test_changed_pdf_section_points_to_file(curated_copy, source):
    lock = pol.read_lock(curated_copy)
    lock["sections"]["4"] = "0" * 64
    (curated_copy / pol.SOURCE_LOCK).write_text(yaml.safe_dump(lock))
    with pytest.raises(pol.PolicyError) as exc:
        _check(curated_copy, source)
    assert "['4']" in str(exc.value)
    assert "04_trocas_devolucoes.md" in str(exc.value)
    assert "emporio policies lock" in str(exc.value)


def test_uncovered_section_is_detected(curated_copy, source):
    (curated_copy / "08_garantia.md").unlink()
    with pytest.raises(pol.PolicyError, match=r"sem curadoria: \['8'\]"):
        _check(curated_copy, source)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("kind: policy", "kind: outro", "front matter inválido"),
        ("id: garantia", "id: horarios", "duplicados"),
        ("---\nid: garantia", "id: garantia", "front matter YAML ausente"),
    ],
)
def test_invalid_curated_file(curated_copy, old, new, message):
    f = curated_copy / "08_garantia.md"
    f.write_text(f.read_text().replace(old, new, 1))
    with pytest.raises(pol.PolicyError, match=message):
        pol.load_curated(curated_copy)


def test_missing_lock(curated_copy):
    (curated_copy / pol.SOURCE_LOCK).unlink()
    with pytest.raises(pol.PolicyError, match="emporio policies lock"):
        pol.read_lock(curated_copy)


def test_bad_curation_fails_only_policy_branch(curated_copy, tmp_path):
    f = curated_copy / "02_horarios.md"
    f.write_text(f.read_text().replace("09:00 às 13:00", "09:00 às 12:00"))
    settings = make_settings(
        data_processed_dir=tmp_path / "p", data_curated_dir=curated_copy.parent
    )
    report = build_pipeline(settings).run().by_name()
    assert report["build_policies"].status == "failed"
    assert "13:00" in (report["build_policies"].error or "")
    assert report["quality_checks"].status == "ok"  # ramo de dados não é afetado


# ============================================================================ busca por assunto


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Posso devolver meu pedido?", "trocas_devolucoes"),
        ("me arrependi da compra", "trocas_devolucoes"),
        ("o produto veio com defeito", "trocas_devolucoes"),
        ("qual o horário de sábado?", "horarios"),
        ("vocês abrem domingo?", "horarios"),
        ("dá pra parcelar em 10x?", "pagamento"),
        ("aceita pix?", "pagamento"),
        ("quanto custa o frete pra Dourados?", "frete_entregas"),
        ("cadê meu código de rastreio", "frete_entregas"),
        ("meu violão quebrou, tem garantia?", "garantia"),
        ("qual o endereço da loja?", "sobre_contato"),
        ("qual o whatsapp de vocês?", "sobre_contato"),
        ("quero apagar meus dados", "privacidade"),
        ("tem promoção na black friday?", "promocoes"),
        ("quero fazer uma reclamação", "atendimento"),
    ],
)
def test_search_top_hit(store, query, expected):
    hits = store.search(query)
    assert hits, f"nenhuma seção para: {query}"
    assert hits[0].section.id == expected


@pytest.mark.parametrize("query", ["receita de bolo de cenoura", "quem ganhou o jogo ontem?", ""])
def test_search_out_of_scope_returns_nothing(store, query):
    assert store.search(query) == []


def test_search_reports_matches_without_duplicates(store):
    hit = store.search("qual o horário de sábado?")[0]
    assert "sabado" in hit.matched
    assert len(hit.matched) == len(set(hit.matched))


def test_store_index_and_missing_file(store, tmp_path):
    assert len(store.index()) == 10
    assert store.get("nao_existe") is None
    with pytest.raises(FileNotFoundError, match="emporio pipeline run"):
        PolicyStore.from_json(tmp_path / "x.json")


# ============================================================================ CLI


def test_cli_policies(built_store, monkeypatch, capsys, curated_copy):
    monkeypatch.setenv("DATA_PROCESSED_DIR", str(built_store.parent))
    monkeypatch.setenv("DATA_CURATED_DIR", str(curated_copy.parent))
    assert main(["policies", "list"]) == 0
    assert "trocas_devolucoes" in capsys.readouterr().out
    assert main(["policies", "search", "posso trocar?"]) == 0
    assert capsys.readouterr().out.split()[1] == "trocas_devolucoes"
    (curated_copy / pol.SOURCE_LOCK).unlink()
    assert main(["policies", "lock"]) == 0
    assert (curated_copy / pol.SOURCE_LOCK).read_text() == (CURATED / pol.SOURCE_LOCK).read_text()
