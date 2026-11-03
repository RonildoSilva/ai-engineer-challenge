"""Gerência dos servidores locais (llamafile).

Reaproveita um servidor que já esteja de pé ou sobe um novo pelo `scripts/start_llamafile.sh`
e o encerra ao final.

- O llamafile é um binário APE e o `subprocess` do Python não o executa diretamente; por isso
  os processos são iniciados pelo script (bash), que é também a única fonte das flags por modo.
- Se a porta configurada estiver ocupada por **outro** serviço, o servidor sobe numa porta livre
  e a URL efetiva é devolvida a quem chamou (8080/8081 são portas muito usadas).
- A saída do servidor vai para `logs/llamafile-<modo>.log`; em caso de falha, a mensagem de erro
  traz as últimas linhas.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from emporio.config import PROJECT_ROOT
from emporio.log import get_logger

log = get_logger(__name__)
START_SCRIPT = PROJECT_ROOT / "scripts" / "start_llamafile.sh"


class ServerUnavailableError(RuntimeError):
    """Servidor fora do ar e não foi possível iniciá-lo (modelo ausente, erro ao subir…)."""


def is_healthy(base_url: str, timeout: float = 2.0) -> bool:
    root = base_url.removesuffix("/").removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{root}/health", timeout=timeout) as r:  # noqa: S310
            return json.load(r).get("status") == "ok"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _with_port(base_url: str, port: int) -> str:
    u = urlparse(base_url)
    return urlunparse(u._replace(netloc=f"{u.hostname}:{port}"))


def _tail(path: Path, lines: int = 8) -> str:
    try:
        content = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


@contextmanager
def ensure_server(
    mode: str,
    base_url: str,
    model_file: Path,
    *,
    log_dir: Path | None = None,
    startup_timeout: float = 120.0,
    alias: str | None = None,
) -> Generator[str, None, None]:
    """Garante um servidor saudável durante o bloco e produz a URL efetiva (`.../v1`).

    Servidor já saudável em `base_url` é reaproveitado e continua rodando ao sair. Caso
    contrário, um novo é iniciado (na porta configurada ou, se ela estiver ocupada por outro
    serviço, numa porta livre) e encerrado ao sair.
    """
    if is_healthy(base_url):
        yield base_url
        return
    if not model_file.is_file():
        raise ServerUnavailableError(
            f"servidor {mode} fora do ar em {base_url} e modelo ausente ({model_file}): "
            "rode ./scripts/setup.sh"
        )
    url = urlparse(base_url)
    host, port = url.hostname or "127.0.0.1", url.port or 8080
    if port_in_use(host, port):
        new_port = free_port(host)
        log.warning(
            "porta ocupada por outro serviço; usando porta livre",
            extra={"mode": mode, "port": port, "new_port": new_port},
        )
        port, base_url = new_port, _with_port(base_url, new_port)

    log_dir = log_dir or PROJECT_ROOT / "data" / "processed" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"llamafile-{mode}.log"
    env = {**os.environ, "MODE": mode, "HOST": host, "PORT": str(port), "MODEL": str(model_file)}
    if alias:
        env["ALIAS"] = alias  # nome do modelo na API (o perfil o envia em cada requisição)
    log.info("subindo servidor llamafile", extra={"mode": mode, "url": base_url})
    with log_file.open("w") as out:
        proc = subprocess.Popen(  # noqa: S603 — script do próprio projeto, sem entrada do usuário
            ["/bin/bash", str(START_SCRIPT)],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # permite encerrar o grupo inteiro de processos
        )
    try:
        deadline = time.monotonic() + startup_timeout
        while not is_healthy(base_url):
            if proc.poll() is not None:
                raise ServerUnavailableError(
                    f"servidor {mode} encerrou ao iniciar (código {proc.returncode}); "
                    f"log em {log_file}:\n{_tail(log_file)}"
                )
            if time.monotonic() > deadline:
                raise ServerUnavailableError(
                    f"servidor {mode} não ficou pronto em {startup_timeout:.0f} s; "
                    f"log em {log_file}"
                )
            time.sleep(0.5)
        yield base_url
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        log.info("servidor llamafile encerrado", extra={"mode": mode})
