"""Cliente LLM agnóstico de provedor, configurado por perfis (`config/llm_profiles.yaml`).

Qualquer servidor compatível com OpenAI serve: llamafile/llama.cpp local, Ollama, APIs online.
O cliente normaliza o que os modelos pequenos às vezes devolvem fora do padrão:
- argumentos de tool com JSON inválido viram `ToolCall.error` (o loop do agente devolve o erro ao
  modelo em vez de quebrar);
- tool call escrita no texto (`<tool_call>{…}</tool_call>`) quando o servidor não a estruturou;
- bloco de raciocínio `<think>…</think>` removido do texto final.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx2
import openai
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from emporio.config import Settings
from emporio.log import get_logger

log = get_logger(__name__)


class LLMConfigError(ValueError):
    """Perfil inexistente, arquivo inválido ou chave de API ausente."""


class LLMUnavailableError(RuntimeError):
    """O servidor do LLM não respondeu (fora do ar, timeout, erro 5xx)."""


# ------------------------------------------------------------------------------ perfis


class LocalServer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["chat"] = "chat"
    model_file: Path


class LLMProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    base_url: str
    model: str
    model_env: str | None = None  # variável que, se definida, sobrescreve o modelo
    api_key: str | None = None
    api_key_env: str | None = None
    context_window: int | Literal["auto"] = 8192
    fallback_context_window: int = Field(default=8192, gt=0)
    max_output_tokens: int = Field(default=512, gt=0)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    supports_tools: bool = True
    timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    local: LocalServer | None = None
    tested: bool = False

    def resolved_model(self) -> str:
        return os.environ.get(self.model_env, self.model) if self.model_env else self.model

    def resolved_api_key(self) -> str:
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise LLMConfigError(
                    f"o perfil '{self.name}' precisa da variável {self.api_key_env} (chave de API)"
                )
            return key
        return self.api_key or "none"


class ProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str
    profiles: dict[str, LLMProfile]


def load_profiles(path: Path) -> ProfilesFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        data = ProfilesFile.model_validate(raw)
    except FileNotFoundError as e:
        raise LLMConfigError(f"arquivo de perfis não encontrado: {path}") from e
    except (yaml.YAMLError, ValidationError) as e:
        raise LLMConfigError(f"{path}: perfis inválidos: {e}") from e
    if data.default not in data.profiles:
        raise LLMConfigError(f"{path}: perfil padrão '{data.default}' não está em `profiles`")
    for name, profile in data.profiles.items():
        profile.name = name
    return data


def get_profile(settings: Settings, name: str | None = None) -> LLMProfile:
    """Perfil ativo (argumento > LLM_PROFILE > default), com sobrescritas LLM_* do ambiente."""
    data = load_profiles(settings.path(settings.llm_profiles_file))
    chosen = name or settings.llm_profile or data.default
    if chosen not in data.profiles:
        raise LLMConfigError(f"perfil '{chosen}' não existe. Perfis: {', '.join(data.profiles)}")
    overrides = {
        "base_url": settings.llm_base_url,
        "model": settings.llm_model,
        "api_key": settings.llm_api_key,
        "temperature": settings.llm_temperature,
    }
    return data.profiles[chosen].model_copy(
        update={k: v for k, v in overrides.items() if v is not None}
    )


# ------------------------------------------------------------------------------ respostas


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str
    error: str | None = None  # JSON inválido: o loop do agente devolve isso ao modelo


@dataclass(frozen=True)
class ChatResult:
    content: str
    tool_calls: list[ToolCall]
    finish_reason: str | None
    usage: dict[str, int] = field(default_factory=dict)
    latency_s: float = 0.0


_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_BARE_JSON = re.compile(r"^\s*(?:```(?:json)?\s*)?(\{.*\})\s*(?:```)?\s*$", re.DOTALL)
_TEXT_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _parse_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
    text = (raw or "").strip()
    if not text:
        return {}, None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)  # cercas de código
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        return {}, f"argumentos não são JSON válido ({e.msg}): {raw}"
    if not isinstance(value, dict):
        return {}, f"argumentos devem ser um objeto JSON, não {type(value).__name__}"
    return value, None


def _tool_calls_from_text(content: str) -> tuple[str, list[ToolCall]]:
    """Tool calls escritas como texto pelo modelo (formato Qwen/Hermes).

    Acontece quando o servidor não as estruturou. Remove os blocos do texto e devolve as chamadas.
    """
    calls: list[ToolCall] = []
    for i, m in enumerate(_TEXT_TOOL_CALL.finditer(content)):
        payload, error = _parse_arguments(m.group(1))
        name = payload.get("name", "") if not error else ""
        args = payload.get("arguments", {}) if not error else {}
        if isinstance(args, str):
            args, error = _parse_arguments(args)
        calls.append(ToolCall(f"text-{i}", name, args, m.group(1), error))
    return _TEXT_TOOL_CALL.sub("", content).strip(), calls


def _bare_json_tool_call(content: str) -> ToolCall | None:
    """Resposta que é só `{"name": …, "arguments": …}`: uma tool call escrita como texto.

    Visto no Bonsai com 8 tools no prompt: a intenção estava certa, mas sem as tags.
    """
    m = _BARE_JSON.match(content)
    if not m:
        return None
    payload, error = _parse_arguments(m.group(1))
    if error or not isinstance(payload.get("name"), str):
        return None
    args = payload.get("arguments", payload.get("parameters", {}))
    if isinstance(args, str):
        args, error = _parse_arguments(args)
    if not isinstance(args, dict):
        args, error = {}, "argumentos devem ser um objeto JSON"
    return ToolCall("text-0", payload["name"], args, m.group(1), error)


# ------------------------------------------------------------------------------ cliente


class LLMClient:
    def __init__(self, profile: LLMProfile, *, http_client: httpx2.Client | None = None):
        self.profile = profile
        self._http = http_client or httpx2.Client(timeout=profile.timeout_s)
        self._client = openai.OpenAI(
            base_url=profile.base_url,
            api_key=profile.resolved_api_key(),
            timeout=profile.timeout_s,
            max_retries=profile.max_retries,
            http_client=self._http,
        )
        self._context_window: int | None = None

    @property
    def root_url(self) -> str:
        return self.profile.base_url.rstrip("/").removesuffix("/v1")

    def context_window(self) -> int:
        """Janela de contexto: fixa no perfil ou lida de /props (llama.cpp/llamafile)."""
        if self._context_window is None:
            if self.profile.context_window == "auto":
                self._context_window = self._detect_context_window()
            else:
                self._context_window = self.profile.context_window
        return self._context_window

    def _detect_context_window(self) -> int:
        try:
            r = self._http.get(f"{self.root_url}/props", timeout=5)
            r.raise_for_status()
            n_ctx = int(r.json()["default_generation_settings"]["n_ctx"])
        except (httpx2.HTTPError, KeyError, ValueError, TypeError) as e:
            log.warning(
                "não foi possível ler /props; usando o contexto padrão do perfil",
                extra={"profile": self.profile.name, "reason": str(e)},
            )
            return self.profile.fallback_context_window
        return n_ctx

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {
            "model": self.profile.resolved_model(),
            "messages": messages,
            "temperature": self.profile.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.profile.max_output_tokens,
        }
        if tools and self.profile.supports_tools:
            kwargs["tools"] = tools
        t0 = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as e:
            raise LLMUnavailableError(
                f"LLM indisponível ({self.profile.name} em {self.profile.base_url}): {e}"
            ) from e
        latency = time.perf_counter() - t0
        choice = resp.choices[0]
        msg = choice.message
        calls = []
        for c in msg.tool_calls or []:
            if c.type != "function":
                continue
            args, error = _parse_arguments(c.function.arguments)
            calls.append(ToolCall(c.id, c.function.name, args, c.function.arguments or "", error))
        content = _THINK.sub("", msg.content or "").strip()
        if not calls and "<tool_call>" in content:
            content, calls = _tool_calls_from_text(content)
        if not calls and tools and (bare := _bare_json_tool_call(content)):
            content, calls = "", [bare]
        usage = resp.usage.model_dump() if resp.usage else {}
        log.info(
            "chamada ao LLM",
            extra={
                "profile": self.profile.name,
                "latency_s": round(latency, 2),
                "tool_calls": [c.name for c in calls],
                "tokens": usage.get("total_tokens"),
            },
        )
        return ChatResult(
            content,
            calls,
            choice.finish_reason,
            {k: v for k, v in usage.items() if isinstance(v, int)},
            latency,
        )

    def ping(self) -> bool:
        """Servidor respondendo? (`/health` em llama.cpp; listagem de modelos nos demais)."""
        try:
            r = self._http.get(f"{self.root_url}/health", timeout=3)
            if r.status_code == 200:  # noqa: PLR2004
                return True
            self._client.models.list()
        except (httpx2.HTTPError, openai.APIError):
            return False
        return True
