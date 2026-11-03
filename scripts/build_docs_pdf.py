"""Gera um PDF da documentação (README + docs) com os diagramas Mermaid renderizados.

    uv run python scripts/build_docs_pdf.py README.en.md docs/architecture.en.md \
        examples/README.en.md -o "docs/Ronildo Silva - AI Engineer.pdf"

Etapas: (1) cada bloco ```mermaid vira um SVG, renderizado pelo mermaid-cli em container (imagem
fixada por digest); (2) pandoc junta os arquivos num HTML com estilo próprio; (3) o Chrome headless
imprime o HTML em PDF (A4). Requer docker, pandoc e Google Chrome/Chromium.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERMAID_IMAGE = (
    "minlag/mermaid-cli:11.4.2@sha256:"
    "99c983b3ab4e14033f2880bc1b9de17e5090b4515dabd63fe9cf8c0ae6130956"
)
# Mais largura antes de quebrar rótulos e margem para os títulos de subgrafo: com o padrão (200 px),
# títulos longos quebravam em duas linhas e a segunda passava por cima das caixas.
MERMAID_CONFIG = (
    '{"flowchart": {"wrappingWidth": 420, "subGraphTitleMargin": {"top": 6, "bottom": 10},'
    ' "nodeSpacing": 40, "rankSpacing": 45}}'
)
BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
REPO_URL = "https://github.com/RonildoSilva/ai-engineer-challenge/blob/main"
# badge de CI: imagem ao vivo, mostraria o estado do CI no momento da geração e não o do commit
BADGE = re.compile(r"^\[!\[[^\]]*\]\([^)]*badge[^)]*\)\]\([^)]*\)\n+", re.MULTILINE)

CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: "Inter", "Segoe UI", system-ui, sans-serif; font-size: 10.2pt;
       line-height: 1.45; color: #1d2027; max-width: none; }
p, li, blockquote { text-align: justify; hyphens: auto; -webkit-hyphens: auto; }
td, th { text-align: left; hyphens: auto; }
h1 { font-size: 20pt; border-bottom: 3px solid #b8722c; padding-bottom: 4px; margin-top: 0; }
h1, h2, h3 { font-family: "Lora", Georgia, serif; color: #1d2027; page-break-after: avoid; }
h2 { font-size: 14.5pt; border-bottom: 1px solid #e6ddd0; padding-bottom: 3px; margin-top: 22px; }
h3 { font-size: 12pt; margin-top: 16px; }
a { color: #9a5a1c; text-decoration: none; }
code { font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 8.6pt;
       background: #f4efe8; padding: 1px 3px; border-radius: 3px; }
pre { background: #f4efe8; padding: 8px 10px; border-radius: 6px; overflow-x: hidden;
      white-space: pre-wrap; word-break: break-word; font-size: 8.4pt; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 8.6pt;
        page-break-inside: auto; }
th, td { border: 1px solid #e6ddd0; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #f7f3ec; }
tr { page-break-inside: avoid; }
blockquote { border-left: 3px solid #b8722c; margin: 8px 0; padding: 2px 12px; color: #4a4339;
             background: #fbf8f3; }
img { max-width: 100%; }
figure.diagram { margin: 10px 0; text-align: center; page-break-inside: avoid; }
figure.diagram img { max-height: 210mm; }
.doc-break { page-break-before: always; }
"""


def render_mermaid(md: str, out_dir: Path) -> str:
    """Troca cada bloco mermaid por uma imagem SVG renderizada (cache por conteúdo)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(
        0o777
    )  # o container roda com o usuário atual, mas o diretório precisa ser gravável

    def replace(m: re.Match[str]) -> str:
        code = m.group(1)
        name = hashlib.sha256((MERMAID_CONFIG + code).encode()).hexdigest()[:16]
        svg = out_dir / f"{name}.svg"
        if not svg.exists():
            (out_dir / f"{name}.mmd").write_text(code, encoding="utf-8")
            (out_dir / "config.json").write_text(MERMAID_CONFIG, encoding="utf-8")
            subprocess.run(  # noqa: S603 — comando fixo, entradas geradas aqui
                ["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}",  # noqa: S607
                 "-v", f"{out_dir}:/data", MERMAID_IMAGE, "-i", f"/data/{name}.mmd",
                 "-o", f"/data/{name}.svg", "-b", "white", "-c", "/data/config.json"],
                check=True, capture_output=True,
            )  # fmt: skip
        return f'<figure class="diagram"><img src="{svg}" alt="diagram"></figure>\n'

    return BLOCK.sub(replace, md)


def absolute_images(md: str, base: Path) -> str:
    def fix(m: re.Match[str]) -> str:
        return f"]({(base / m.group(1)).resolve()})"

    return re.sub(r"\]\((?!https?:|#)([^)]+\.(?:png|svg|jpg))\)", fix, md)


def repo_links(md: str, base: Path) -> str:
    """Links relativos para outros arquivos do repositório → URL no GitHub (no PDF não há repo)."""

    def fix(m: re.Match[str]) -> str:
        target, anchor = m.group(1), m.group(2) or ""
        rel = (base / target).resolve().relative_to(ROOT)
        return f"]({REPO_URL}/{rel.as_posix()}{anchor})"

    return re.sub(r"\]\((?!https?:|#|/)([^)#\s]+?)(#[^)\s]*)?\)", fix, md)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help="arquivos Markdown, na ordem")
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--title", default="Empório da Música — Customer Service Agent")
    args = ap.parse_args()

    chrome = next((c for c in ("google-chrome", "chromium", "chromium-browser") if shutil.which(c)),
                  None)  # fmt: skip
    if not chrome or not shutil.which("pandoc"):
        raise SystemExit("precisa de pandoc e Google Chrome/Chromium no PATH")

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        parts = []
        for i, f in enumerate(args.files):
            md = f.read_text(encoding="utf-8")
            # imagens relativas ao arquivo de origem → caminho absoluto (o HTML fica em outro lugar)
            md = BADGE.sub("", md)
            md = absolute_images(md, f.resolve().parent)
            md = repo_links(md, f.resolve().parent)
            md = render_mermaid(md, ROOT / "data/processed/docs_build/mermaid")
            parts.append(md if i == 0 else f'\n<div class="doc-break"></div>\n\n{md}')
        source = tmp / "doc.md"
        source.write_text("\n".join(parts), encoding="utf-8")
        css = tmp / "style.css"
        css.write_text(CSS, encoding="utf-8")
        html = tmp / "doc.html"
        subprocess.run(  # noqa: S603
            ["pandoc", str(source), "-f", "gfm-tex_math_dollars-tex_math_gfm", "-t", "html5", "-s",  # noqa: S607
             "--metadata", f"pagetitle={args.title}", "--metadata", "lang=en",
             "--css", str(css), "-o", str(html)],
            check=True,
        )  # fmt: skip
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(  # noqa: S603
            [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             "--allow-file-access-from-files", f"--print-to-pdf={args.output.resolve()}",
             f"file://{html}"],
            check=True, capture_output=True,
        )  # fmt: skip
    print(f"✔ {args.output} ({args.output.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
