"""Orquestrador leve de pipeline: DAG de etapas idempotentes, incrementais e com linhagem.

Cada `Stage` declara entradas, saídas e dependências. O `Pipeline`:

- executa em ordem topológica (só as etapas pedidas e as que vêm antes delas);
- **pula** uma etapa quando a impressão digital (fingerprint) não mudou e as saídas
  continuam íntegras. O fingerprint combina o hash das entradas, o código da função da
  etapa, a `version` e os fingerprints das dependências;
- em caso de falha, marca as etapas dependentes como `skipped` e segue com as independentes;
- grava a linhagem (hashes de entradas e saídas, tempos, métricas, erro) num manifesto JSON.

Cada etapa corresponde 1:1 a uma task de um orquestrador como Airflow ou Dagster: a função
da etapa recebe um `StageContext` e não depende deste runner.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any, Literal

from emporio.log import get_logger

log = get_logger(__name__)

Status = Literal["ok", "failed", "skipped"]


class PipelineError(Exception):
    """Erro de definição do pipeline (dependência inexistente, ciclo, nome duplicado)."""


@dataclass(frozen=True)
class StageContext:
    """O que a função da etapa recebe. Não depende do runner (portável para Airflow)."""

    name: str
    root: Path
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    settings: Any = None


@dataclass(frozen=True)
class Stage:
    name: str
    func: Callable[[StageContext], dict[str, Any] | None]
    deps: tuple[str, ...] = ()
    inputs: tuple[Path, ...] = ()
    outputs: tuple[Path, ...] = ()
    version: str = "1"
    description: str = ""
    # módulos/objetos cujo código-fonte também invalida a etapa (ex.: regras usadas por ela)
    code: tuple[Any, ...] = ()


@dataclass
class StageResult:
    name: str
    status: Status | Literal["up_to_date"]
    duration_s: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class RunReport:
    results: list[StageResult]

    @property
    def ok(self) -> bool:
        return all(r.status in ("ok", "up_to_date") for r in self.results)

    def by_name(self) -> dict[str, StageResult]:
        return {r.name: r for r in self.results}


# ------------------------------------------------------------------------------ hashing


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_paths(paths: Iterable[Path], root: Path) -> dict[str, str | None]:
    """Hash de cada arquivo (diretórios são expandidos). Ausente → None."""
    out: dict[str, str | None] = {}
    for p in paths:
        if p.is_dir():
            for f in sorted(x for x in p.rglob("*") if x.is_file()):
                out[_rel(f, root)] = _sha256_file(f)
        elif p.is_file():
            out[_rel(p, root)] = _sha256_file(p)
        else:
            out[_rel(p, root)] = None
    return out


def _rel(p: Path, root: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def _source(obj: Any) -> str:
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError):  # sem fonte disponível (ex.: lambdas no REPL)
        return repr(obj)


def _code_hash(func: Callable[..., Any], extra: Iterable[Any] = ()) -> str:
    h = hashlib.sha256(_source(func).encode())
    for obj in extra:
        h.update(_source(obj).encode())
    return h.hexdigest()


# ------------------------------------------------------------------------------ pipeline


class Pipeline:
    def __init__(
        self,
        stages: Sequence[Stage],
        state_file: Path,
        root: Path,
        settings: Any = None,
    ):
        self.stages = {s.name: s for s in stages}
        if len(self.stages) != len(stages):
            raise PipelineError("nomes de etapa duplicados")
        for s in stages:
            missing = [d for d in s.deps if d not in self.stages]
            if missing:
                raise PipelineError(f"etapa '{s.name}' depende de etapa inexistente: {missing}")
        self.state_file = state_file
        self.root = root
        self.settings = settings
        self._graph = {s.name: set(s.deps) for s in stages}
        try:
            self._order = list(TopologicalSorter(self._graph).static_order())
        except CycleError as e:
            raise PipelineError(f"ciclo entre etapas: {e.args[1]}") from e

    # --------------------------------------------------------------------- consultas

    def order(self, targets: Iterable[str] | None = None) -> list[str]:
        """Ordem topológica das etapas pedidas e de tudo que vem antes delas."""
        if targets is None:
            return list(self._order)
        wanted: set[str] = set()
        stack = list(targets)
        while stack:
            name = stack.pop()
            if name not in self.stages:
                raise PipelineError(f"etapa inexistente: '{name}'")
            if name not in wanted:
                wanted.add(name)
                stack.extend(self.stages[name].deps)
        return [n for n in self._order if n in wanted]

    def load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"stages": {}}

    def _fingerprint(self, stage: Stage, dep_fps: dict[str, str]) -> str:
        """Entradas (estado atual em disco) + código + versão + fingerprints das dependências.

        Precisa ser calculado *depois* que as dependências rodaram, porque as entradas de uma
        etapa costumam ser as saídas da anterior.
        """
        payload = {
            "name": stage.name,
            "version": stage.version,
            "code": _code_hash(stage.func, stage.code),
            "inputs": hash_paths(stage.inputs, self.root),
            "deps": {d: dep_fps[d] for d in sorted(stage.deps)},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def fingerprints(self) -> dict[str, str]:
        """Fingerprint de cada etapa com base no que está em disco agora."""
        fps: dict[str, str] = {}
        for name in self._order:
            fps[name] = self._fingerprint(self.stages[name], fps)
        return fps

    def status(self) -> dict[str, str]:
        """`up_to_date`, `stale`, `failed` ou `never_run` para cada etapa."""
        state = self.load_state()["stages"]
        fps = self.fingerprints()
        out: dict[str, str] = {}
        for name in self._order:
            prev = state.get(name)
            if prev is None:
                out[name] = "never_run"
            elif prev["status"] != "ok":
                out[name] = prev["status"]
            elif self._is_fresh(self.stages[name], prev, fps[name]):
                out[name] = "up_to_date"
            else:
                out[name] = "stale"
        return out

    def mermaid(self) -> str:
        lines = ["flowchart LR"]
        for name in self._order:
            s = self.stages[name]
            label = f"{name}<br/>{s.description}" if s.description else name
            lines.append(f'    {name}["{label}"]')
        lines.extend(
            f"    {d} --> {name}" for name in self._order for d in sorted(self.stages[name].deps)
        )
        return "\n".join(lines)

    # --------------------------------------------------------------------- execução

    def _is_fresh(self, stage: Stage, prev: dict[str, Any], fp: str) -> bool:
        if prev.get("status") != "ok" or prev.get("fingerprint") != fp:
            return False
        # saídas precisam existir e estar iguais ao que a etapa produziu (detecta adulteração)
        return hash_paths(stage.outputs, self.root) == prev.get("outputs", {})

    def run(self, targets: Iterable[str] | None = None, *, force: bool = False) -> RunReport:
        names = self.order(targets)
        state = self.load_state()
        stages_state: dict[str, Any] = state.setdefault("stages", {})
        fps: dict[str, str] = {}  # calculados um a um, depois que as dependências rodaram
        results: list[StageResult] = []
        blocked: set[str] = set()

        for name in names:
            stage = self.stages[name]
            failed_deps = [d for d in stage.deps if d in blocked]
            if failed_deps:
                blocked.add(name)
                msg = f"dependência falhou: {', '.join(failed_deps)}"
                log.warning("etapa pulada", extra={"stage": name, "reason": msg})
                results.append(StageResult(name, "skipped", error=msg))
                stages_state[name] = {**stages_state.get(name, {}), "status": "skipped"}
                continue

            fps[name] = self._fingerprint(stage, fps)
            prev = stages_state.get(name, {})
            if not force and self._is_fresh(stage, prev, fps[name]):
                log.info("etapa em dia (pulada)", extra={"stage": name})
                results.append(StageResult(name, "up_to_date", metrics=prev.get("metrics", {})))
                continue

            result = self._execute(stage, fps[name], stages_state)
            results.append(result)
            if result.status != "ok":
                blocked.add(name)
            self._save_state(state)  # persiste a cada etapa: falha no meio não perde progresso

        self._save_state(state)
        return RunReport(results)

    def _execute(self, stage: Stage, fp: str, stages_state: dict[str, Any]) -> StageResult:
        for out in stage.outputs:  # só o diretório pai; a etapa cria a própria saída
            out.parent.mkdir(parents=True, exist_ok=True)
        ctx = StageContext(stage.name, self.root, stage.inputs, stage.outputs, self.settings)
        started = datetime.now(UTC)
        t0 = time.perf_counter()
        log.info("executando etapa", extra={"stage": stage.name})
        error: str | None = None
        metrics: dict[str, Any] = {}
        try:
            metrics = stage.func(ctx) or {}
        except (
            Exception
        ) as e:  # qualquer falha da etapa vira status 'failed' (com traceback no log)
            error = f"{type(e).__name__}: {e}"
            log.exception("etapa falhou", extra={"stage": stage.name, "error": error})
        else:
            missing = [_rel(o, self.root) for o in stage.outputs if not o.exists()]
            if missing:
                error = f"RuntimeError: saídas declaradas não foram geradas: {missing}"
                log.error("etapa falhou", extra={"stage": stage.name, "error": error})
        duration = round(time.perf_counter() - t0, 3)
        status: Status = "failed" if error else "ok"
        stages_state[stage.name] = {
            "status": status,
            "fingerprint": fp if status == "ok" else None,
            "version": stage.version,
            "inputs": hash_paths(stage.inputs, self.root),
            "outputs": hash_paths(stage.outputs, self.root) if status == "ok" else {},
            "started_at": started.isoformat(),
            "duration_s": duration,
            "metrics": metrics,
            "error": error,
        }
        if status == "ok":
            log.info("etapa concluída", extra={"stage": stage.name, "duration_s": duration})
        return StageResult(stage.name, status, duration, metrics, error)

    def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(UTC).isoformat()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True))
        tmp.replace(self.state_file)  # escrita atômica (rename no mesmo diretório)
