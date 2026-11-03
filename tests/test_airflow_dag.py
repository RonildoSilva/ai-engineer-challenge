"""Template do DAG do Airflow: gerado das mesmas etapas do pipeline, sem precisar do Airflow.

O arquivo real `dags/emporio_dag.py` é importado com um stub mínimo de `airflow` (DAG como
contexto, PythonOperator e `>>`), o bastante para verificar tasks, dependências e callables.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import types
from pathlib import Path

import pytest

from emporio.pipeline.airflow import run_task, task_graph
from emporio.pipeline.core import PipelineError
from emporio.pipeline.stages import build_pipeline
from tests.conftest import ROOT, make_settings

DAG_FILE = ROOT / "dags/emporio_dag.py"


class _Dag:
    current: _Dag | None = None

    def __init__(self, dag_id, **kwargs):
        self.dag_id, self.kwargs = dag_id, kwargs
        self.tasks: dict[str, _Op] = {}
        self.edges: set[tuple[str, str]] = set()

    def __enter__(self):
        _Dag.current = self
        return self

    def __exit__(self, *exc):
        _Dag.current = None


class _Op:
    def __init__(self, task_id, python_callable, op_kwargs, **kwargs):
        self.task_id, self.callable, self.op_kwargs, self.kwargs = (
            task_id, python_callable, op_kwargs, kwargs,
        )  # fmt: skip
        assert _Dag.current is not None, "operador criado fora do DAG"
        self.dag = _Dag.current
        self.dag.tasks[task_id] = self

    def __rshift__(self, other: _Op) -> _Op:
        self.dag.edges.add((self.task_id, other.task_id))
        return other


@pytest.fixture
def dag(monkeypatch):
    airflow = types.ModuleType("airflow")
    setattr(airflow, "DAG", _Dag)  # noqa: B010 (módulo stub)
    ops = types.ModuleType("airflow.operators.python")
    setattr(ops, "PythonOperator", _Op)  # noqa: B010
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.operators", types.ModuleType("airflow.operators"))
    monkeypatch.setitem(sys.modules, "airflow.operators.python", ops)
    spec = importlib.util.spec_from_file_location("emporio_dag", DAG_FILE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # Airflow 3 ausente → cai no import do Airflow 2
    return module.dag


def test_dag_has_one_task_per_stage_with_the_same_dependencies(dag):
    pipe = build_pipeline(make_settings())
    assert set(dag.tasks) == set(pipe.stages)
    expected = {(d, name) for name, s in pipe.stages.items() for d in s.deps}
    assert dag.edges == expected
    assert dag.dag_id == "emporio_pipeline"
    assert dag.kwargs["catchup"] is False
    assert dag.kwargs["max_active_runs"] == 1


def test_each_task_runs_its_own_stage(dag):
    for task_id, op in dag.tasks.items():
        assert op.callable is run_task
        assert op.op_kwargs == {"stage": task_id}


def test_task_graph_is_topological():
    specs = task_graph(make_settings())
    seen: set[str] = set()
    for t in specs:
        assert set(t.upstream) <= seen, t.task_id
        seen.add(t.task_id)


# ============================================================================ run_task


@pytest.fixture
def pipeline_copy(built_store, tmp_path):
    """Cópia do pipeline construído (raw + processed), para não alterar a fixture de sessão."""
    base = built_store.parent.parent
    shutil.copytree(base / "raw", tmp_path / "raw")
    shutil.copytree(base / "processed", tmp_path / "processed")
    return make_settings(data_raw_dir=tmp_path / "raw", data_processed_dir=tmp_path / "processed")


def test_run_task_requires_upstream_tasks_to_be_up_to_date(pipeline_copy):
    """A task não roda as anteriores por conta própria: no Airflow, quem garante a ordem é o DAG.

    Numa cópia em outro lugar do disco, as entradas mudaram e nada está em dia.
    """
    with pytest.raises(PipelineError, match="não está em dia"):
        run_task("transform", pipeline_copy)


def test_running_tasks_in_dag_order_rebuilds_then_skips(pipeline_copy):
    results = [run_task(t.task_id, pipeline_copy) for t in task_graph(pipeline_copy)]
    assert all(r["status"] == "ok" for r in results), results
    assert run_task("load", pipeline_copy)["status"] == "up_to_date"  # incremental, como o CLI
    assert Path(pipeline_copy.store_db).is_file()


def test_failed_stage_raises_for_the_orchestrator(pipeline_copy):
    for t in task_graph(pipeline_copy):
        run_task(t.task_id, pipeline_copy)
    (Path(pipeline_copy.raw_dir) / "products.csv").write_text("quebrado\n", encoding="utf-8")
    run_task("snapshot_raw", pipeline_copy)
    with pytest.raises(PipelineError, match="validate_raw"):
        run_task("validate_raw", pipeline_copy)
