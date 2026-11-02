"""Lado cliente do MCP: conecta ao servidor da loja e expõe as tools no formato do LLM.

O agente só conhece o servidor pelo protocolo MCP. Dois modos:
- em processo (`Client(build_server(...))`): sem subprocesso, usado no chat e nos testes;
- stdio (`emporio mcp` como subprocesso): o mesmo caminho que um cliente externo usaria.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

from emporio.log import get_logger

log = get_logger(__name__)


def compact(value: Any) -> Any:
    """Remove nulos, listas e dicionários vazios: menos tokens no contexto do modelo."""
    if isinstance(value, dict):
        out = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, [], {}, "")}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] | None  # saída estruturada (já compactada)
    text: str  # o que vai para o modelo (JSON compacto ou mensagem de erro)


class McpToolbox:
    """Sessão MCP aberta durante a conversa (use com `async with`)."""

    def __init__(self, target: Any):
        self._target = target  # MCPServer, StdioServerParameters ou URL
        self._stack = AsyncExitStack()
        self._client: Client | None = None
        self.tools: list[dict[str, Any]] = []  # formato OpenAI `tools`
        self.tool_names: set[str] = set()

    @classmethod
    def stdio(cls, command: str, args: list[str], env: dict[str, str]) -> McpToolbox:
        return cls(StdioServerParameters(command=command, args=args, env=env))

    async def __aenter__(self) -> McpToolbox:
        self._client = await self._stack.enter_async_context(Client(self._target))
        listed = (await self._client.list_tools()).tools
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.input_schema,
                },
            }
            for t in listed
        ]
        self.tool_names = {t.name for t in listed}
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._stack.aclose()

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("McpToolbox precisa ser usado com `async with`")
        return self._client

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        r = await self.client.call_tool(name, arguments)
        texts = [c.text for c in r.content if isinstance(c, TextContent)]
        if r.is_error:
            error = " ".join(texts) or "erro ao executar a ferramenta"
            return ToolResult(ok=False, data=None, text=error)
        data = compact(r.structured_content) if r.structured_content else None
        text = (
            json.dumps(data, ensure_ascii=False, separators=(",", ":")) if data else " ".join(texts)
        )
        return ToolResult(ok=True, data=data, text=text)

    async def persona(self, customer_first_name: str | None = None) -> str:
        args = {"nome_cliente": customer_first_name} if customer_first_name else {}
        prompt = await self.client.get_prompt("persona_atendente", args)
        content = prompt.messages[0].content
        return content.text if isinstance(content, TextContent) else ""
