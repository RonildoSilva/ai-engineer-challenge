"""Servidor da interface web (`emporio web`): o build do Vite (`web/dist`) + `config.js` gerado.

A interface é React + Tailwind em `web/` (build com `npm --prefix web ci && npm --prefix web run
build`, feito pelo `setup.sh` quando há Node). Este servidor é para o uso local; no compose, um
nginx serve o mesmo build.
"""

from __future__ import annotations

import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles


def build_web_app(web_dir: Path, api_url: str) -> Starlette:
    """`config.js` aponta a interface para `api_url`; o resto vem de `web_dir`."""
    if not (web_dir / "index.html").is_file():
        raise FileNotFoundError(
            f"{web_dir}/index.html não existe: rode "
            "`npm --prefix web ci && npm --prefix web run build`"
        )

    async def config_js(_: Request) -> Response:
        body = f"window.EMPORIO_API = {json.dumps(api_url.rstrip('/'))};\n"
        return Response(body, media_type="text/javascript", headers={"Cache-Control": "no-store"})

    return Starlette(
        routes=[
            Route("/config.js", config_js),
            Mount("/", StaticFiles(directory=web_dir, html=True)),
        ]
    )
