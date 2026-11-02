"""Smoke test do LLM configurado no .env: chat simples + uma chamada de ferramenta.

Uso:  uv run python scripts/smoke_llm.py
"""

import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.getenv("LLM_BASE_URL", "http://localhost:8080/v1"),
    api_key=os.getenv("LLM_API_KEY", "none"),
)
MODEL = os.getenv("LLM_MODEL", "bonsai")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "detalhar_produto",
            "description": "Busca preço, estoque e promoção de um produto pelo nome.",
            "parameters": {
                "type": "object",
                "properties": {"nome": {"type": "string"}},
                "required": ["nome"],
            },
        },
    }
]
SYSTEM = "Você é atendente da loja Empório da Música. Responda em português do Brasil."


def run(question: str, tools: list | None = None) -> None:
    t0 = time.perf_counter()
    extra = {"tools": tools} if tools else {}  # SDK novo não aceita tools=None
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        **extra,
    )
    msg = resp.choices[0].message
    calls = [
        (c.function.name, json.loads(c.function.arguments))
        for c in msg.tool_calls or []
        if c.type == "function"
    ]
    print(f"[{time.perf_counter() - t0:5.1f}s] {question}")
    print(f"         tool_calls={calls}")
    print(f"         content={(msg.content or '').strip()[:200]!r}\n")


if __name__ == "__main__":
    print(f"modelo={MODEL} @ {client.base_url}\n")
    run("Oi! Qual é o seu papel?")
    run("Quanto custa o Takamine GD20?", TOOLS)
