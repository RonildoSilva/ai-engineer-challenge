"""Testes do orquestrador: ordem, validação do DAG, idempotência, invalidação, falhas, linhagem."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from emporio.pipeline.core import Pipeline, PipelineError, Stage, StageContext

CALLS: list[str] = []


def _copy(ctx: StageContext) -> dict[str, int]:
    """Concatena as entradas na saída (etapa genérica de teste)."""
    CALLS.append(ctx.name)
    data = b"".join(p.read_bytes() for p in ctx.inputs if p.is_file())
    ctx.outputs[0].write_bytes(data + ctx.name.encode())
    return {"bytes": len(data)}


def _boom(ctx: StageContext) -> None:
    CALLS.append(ctx.name)
    raise ValueError("falha proposital")


def _no_output(ctx: StageContext) -> None:
    CALLS.append(ctx.name)


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()


@pytest.fixture
def tree(tmp_path: Path):
    """DAG:  a → b → d ;  a → c → d ;  e independente."""
    src = tmp_path / "src.txt"
    src.write_text("v1")
    out = tmp_path / "out"

    def make(**overrides) -> Pipeline:
        stages = {
            "a": Stage("a", _copy, inputs=(src,), outputs=(out / "a",)),
            "b": Stage("b", _copy, deps=("a",), inputs=(out / "a",), outputs=(out / "b",)),
            "c": Stage("c", _copy, deps=("a",), inputs=(out / "a",), outputs=(out / "c",)),
            "d": Stage(
                "d", _copy, deps=("b", "c"), inputs=(out / "b", out / "c"), outputs=(out / "d",)
            ),
            "e": Stage("e", _copy, inputs=(src,), outputs=(out / "e",)),
        }
        stages.update(overrides)
        return Pipeline(list(stages.values()), tmp_path / "manifest.json", tmp_path)

    return make, src, out, tmp_path


# ------------------------------------------------------------------------ definição do DAG


def test_topological_order(tree):
    make, *_ = tree
    order = make().order()
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_order_with_targets_includes_only_upstream(tree):
    make, *_ = tree
    assert make().order(["b"]) == ["a", "b"]


def test_unknown_dependency_rejected(tmp_path):
    with pytest.raises(PipelineError, match="inexistente"):
        Pipeline([Stage("x", _copy, deps=("nada",))], tmp_path / "m.json", tmp_path)


def test_cycle_rejected(tmp_path):
    stages = [Stage("x", _copy, deps=("y",)), Stage("y", _copy, deps=("x",))]
    with pytest.raises(PipelineError, match="ciclo"):
        Pipeline(stages, tmp_path / "m.json", tmp_path)


def test_duplicate_names_rejected(tmp_path):
    with pytest.raises(PipelineError, match="duplicad"):
        Pipeline([Stage("x", _copy), Stage("x", _copy)], tmp_path / "m.json", tmp_path)


def test_unknown_target_rejected(tree):
    make, *_ = tree
    with pytest.raises(PipelineError, match="inexistente"):
        make().run(["zzz"])


# ------------------------------------------------------------------------ execução incremental


def test_first_run_executes_all_in_order(tree):
    make, *_ = tree
    report = make().run()
    assert report.ok
    assert set(CALLS) == {"a", "b", "c", "d", "e"} and CALLS.index("a") < CALLS.index("d")


def test_second_run_is_noop(tree):
    make, *_ = tree
    make().run()
    CALLS.clear()
    report = make().run()
    assert CALLS == []
    assert {r.status for r in report.results} == {"up_to_date"}


def test_input_change_invalidates_downstream_only(tree):
    make, src, out, _ = tree
    make().run()
    CALLS.clear()
    # saída de b apagada: b refaz e gera o MESMO conteúdo → d continua em dia (cache por conteúdo)
    (out / "b").unlink()
    make().run()
    assert CALLS == ["b"]
    CALLS.clear()
    # lógica de b muda (versão): b refaz e, como depende de b, d também; a, c, e não
    p = make(
        b=Stage("b", _copy, deps=("a",), inputs=(out / "a",), outputs=(out / "b",), version="2")
    )
    p.run()
    assert sorted(CALLS) == ["b", "d"]
    CALLS.clear()
    src.write_text("v2")  # fonte muda: tudo que depende dela refaz
    make().run()
    assert sorted(CALLS) == ["a", "b", "c", "d", "e"]


def test_tampered_output_is_detected(tree):
    make, _src, out, _ = tree
    make().run()
    CALLS.clear()
    (out / "c").write_text("adulterado")
    assert make().status()["c"] == "stale"
    make().run()
    assert "c" in CALLS


def test_code_or_version_change_invalidates(tree):
    make, *_ = tree
    make().run()
    CALLS.clear()
    p = make(
        e=Stage(
            "e",
            _copy,
            inputs=make().stages["e"].inputs,
            outputs=make().stages["e"].outputs,
            version="2",
        )
    )
    p.run()
    assert CALLS == ["e"]


def test_force_reruns_everything(tree):
    make, *_ = tree
    make().run()
    CALLS.clear()
    make().run(force=True)
    assert sorted(CALLS) == ["a", "b", "c", "d", "e"]


def test_targets_run_only_upstream(tree):
    make, *_ = tree
    make().run(["c"])
    assert sorted(CALLS) == ["a", "c"]


# ------------------------------------------------------------------------ falhas


def test_failure_skips_dependents_but_runs_independents(tree):
    make, src, out, tmp = tree
    p = make(b=Stage("b", _boom, deps=("a",), inputs=(out / "a",), outputs=(out / "b",)))
    report = p.run()
    st = report.by_name()
    assert not report.ok
    assert st["b"].status == "failed" and "falha proposital" in (st["b"].error or "")
    assert st["d"].status == "skipped" and "b" in (st["d"].error or "")
    assert st["c"].status == "ok" and st["e"].status == "ok"
    assert "d" not in CALLS
    state = json.loads((tmp / "manifest.json").read_text())["stages"]
    assert state["b"]["status"] == "failed" and state["b"]["fingerprint"] is None


def test_failed_stage_reruns_next_time(tree):
    make, _src, out, _ = tree
    make(b=Stage("b", _boom, deps=("a",), inputs=(out / "a",), outputs=(out / "b",))).run()
    CALLS.clear()
    assert make().run().ok
    assert sorted(CALLS) == ["b", "d"]


def test_missing_declared_output_fails(tmp_path):
    p = Pipeline(
        [Stage("x", _no_output, outputs=(tmp_path / "nunca.txt",))],
        tmp_path / "m.json",
        tmp_path,
    )
    report = p.run()
    assert report.by_name()["x"].status == "failed"
    assert "não foram geradas" in (report.by_name()["x"].error or "")


# ------------------------------------------------------------------------ linhagem e status


def test_lineage_manifest(tree):
    make, *_ = tree
    make().run()
    state = json.loads((tree[3] / "manifest.json").read_text())
    a = state["stages"]["a"]
    assert a["status"] == "ok" and len(a["fingerprint"]) == 64
    assert a["inputs"] == {"src.txt": a["inputs"]["src.txt"]} and a["outputs"]["out/a"]
    assert a["metrics"] == {"bytes": 2} and "updated_at" in state


def test_status_lifecycle(tree):
    make, src, *_ = tree
    assert set(make().status().values()) == {"never_run"}
    make().run()
    assert set(make().status().values()) == {"up_to_date"}
    src.write_text("v2")
    assert make().status()["a"] == "stale"


def test_mermaid_graph(tree):
    make, *_ = tree
    g = make().mermaid()
    assert g.startswith("flowchart LR") and "a --> b" in g and "c --> d" in g
