"""Cliente LLM: perfis, sobrescritas, contexto via /props e normalização das respostas."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from emporio.agent.llm import (
    LLMClient,
    LLMConfigError,
    LLMProfile,
    LLMUnavailableError,
    get_profile,
    load_profiles,
)
from tests.conftest import ROOT, make_settings

PROFILES = ROOT / "config/llm_profiles.yaml"


# ============================================================================ perfis


def test_project_profiles_are_valid():
    data = load_profiles(PROFILES)
    assert data.default == "bonsai"
    bonsai = data.profiles["bonsai"]
    assert bonsai.tested
    assert bonsai.context_window == "auto"
    assert bonsai.local is not None
    assert (ROOT / bonsai.local.model_file).name == "Bonsai-1.7B.gguf"
    online = [p for p in data.profiles.values() if p.api_key_env]
    assert online
    assert all(not p.tested for p in online)  # sem chave aqui: são referência, não validados


def test_profile_selection_and_overrides(monkeypatch):
    monkeypatch.delenv("LLM_PROFILE", raising=False)
    assert get_profile(make_settings()).name == "bonsai"
    assert get_profile(make_settings(llm_profile="openai")).name == "openai"
    assert get_profile(make_settings(), "anthropic").name == "anthropic"
    p = get_profile(make_settings(llm_base_url="http://127.0.0.1:9999/v1", llm_temperature=0.0))
    assert p.base_url == "http://127.0.0.1:9999/v1"
    assert p.temperature == 0.0
    # um perfil pedido por nome que não é o ativo não herda as sobrescritas do .env
    env = make_settings(llm_base_url="http://localhost:8080/v1", llm_model="bonsai")
    qwen = get_profile(env, "qwen3-1.7b")
    assert qwen.base_url == "http://127.0.0.1:8082/v1"
    assert qwen.model == "qwen3-1.7b"
    assert get_profile(env, "bonsai").base_url == "http://localhost:8080/v1"  # ativo: herda


def test_unknown_profile():
    with pytest.raises(LLMConfigError, match="não existe"):
        get_profile(make_settings(), "nao-existe")


def test_missing_api_key_is_clear(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        get_profile(make_settings(), "anthropic").resolved_api_key()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")
    assert get_profile(make_settings(), "anthropic").resolved_api_key() == "sk-teste"


def test_model_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "modelo-x")
    assert get_profile(make_settings(), "openai").resolved_model() == "modelo-x"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("default: x\nprofiles: {}\n", "padrão"),
        ("default: a\nprofiles:\n  a: {base_url: u}\n", "inválidos"),  # falta model
        ("default: a\nprofiles:\n  a: {base_url: u, model: m, extra: 1}\n", "inválidos"),
    ],
)
def test_invalid_profiles_file(tmp_path: Path, content, message):
    f = tmp_path / "p.yaml"
    f.write_text(content)
    with pytest.raises(LLMConfigError, match=message):
        load_profiles(f)


def test_missing_profiles_file(tmp_path):
    with pytest.raises(LLMConfigError, match="não encontrado"):
        load_profiles(tmp_path / "x.yaml")


# ============================================================================ servidor falso


def _completion(message: dict, finish="stop") -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }  # fmt: skip


class FakeServer:
    """Simula um servidor compatível com OpenAI e registra as requisições."""

    def __init__(self, message: dict | None = None, props: dict | None = None, status: int = 200):
        self.message = message or {"content": "olá"}
        self.props = props
        self.status = status
        self.requests: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/props"):
            if self.props is None:
                return httpx2.Response(404)
            return httpx2.Response(200, json=self.props)
        if request.url.path.endswith("/health"):
            return httpx2.Response(200, json={"status": "ok"})
        if self.status != 200:
            return httpx2.Response(self.status, json={"error": {"message": "falhou"}})
        self.requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_completion(self.message))


def client(server: FakeServer, **profile) -> LLMClient:
    p = LLMProfile(name="t", base_url="http://fake:8080/v1", model="m", max_retries=0, **profile)
    return LLMClient(p, http_client=httpx2.Client(transport=httpx2.MockTransport(server)))


# ============================================================================ contexto


def test_context_window_from_props():
    srv = FakeServer(props={"default_generation_settings": {"n_ctx": 8192}})
    assert client(srv, context_window="auto").context_window() == 8192


def test_context_window_fallback_when_props_unavailable():
    c = client(FakeServer(props=None), context_window="auto", fallback_context_window=4096)
    assert c.context_window() == 4096


def test_fixed_context_window():
    assert client(FakeServer(), context_window=128000).context_window() == 128000


# ============================================================================ chat


def test_chat_uses_profile_defaults():
    srv = FakeServer()
    r = client(srv, temperature=0.3, max_output_tokens=256).chat(
        [{"role": "user", "content": "oi"}]
    )
    assert r.content == "olá"
    assert r.usage["total_tokens"] == 15
    sent = srv.requests[0]
    assert sent["temperature"] == 0.3
    assert sent["max_tokens"] == 256
    assert "tools" not in sent


def test_tools_sent_only_when_supported():
    tool = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
    srv = FakeServer()
    client(srv).chat([{"role": "user", "content": "oi"}], tools=[tool])
    client(srv, supports_tools=False).chat([{"role": "user", "content": "oi"}], tools=[tool])
    assert "tools" in srv.requests[0]
    assert "tools" not in srv.requests[1]


def _tool_message(arguments: str) -> dict:
    return {
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "detalhar_produto", "arguments": arguments}}],
    }  # fmt: skip


def test_structured_tool_call():
    r = client(FakeServer(_tool_message('{"produto": "Takamine GD20"}'))).chat([])
    assert r.tool_calls[0].name == "detalhar_produto"
    assert r.tool_calls[0].arguments == {"produto": "Takamine GD20"}
    assert r.tool_calls[0].error is None


@pytest.mark.parametrize(
    ("raw", "args", "has_error"),
    [
        ('```json\n{"produto": "x"}\n```', {"produto": "x"}, False),  # cerca de código
        ("", {}, False),
        ('{"produto": ', {}, True),  # JSON truncado
        ('["x"]', {}, True),  # não é objeto
    ],
)
def test_tool_call_argument_normalization(raw, args, has_error):
    call = client(FakeServer(_tool_message(raw))).chat([]).tool_calls[0]
    assert call.arguments == args
    assert (call.error is not None) is has_error


def test_tool_call_written_as_text_is_recovered():
    text = 'Vou verificar.\n<tool_call>\n{"name": "status_loja", "arguments": {}}\n</tool_call>'
    r = client(FakeServer({"content": text})).chat([])
    assert [c.name for c in r.tool_calls] == ["status_loja"]
    assert r.content == "Vou verificar."


def test_think_block_is_removed():
    r = client(FakeServer({"content": "<think>\nraciocínio\n</think>\n\nResposta final"})).chat([])
    assert r.content == "Resposta final"


def test_unavailable_server_raises_clear_error():
    def down(_request):
        raise httpx2.ConnectError("conexão recusada")

    p = LLMProfile(name="t", base_url="http://fake:1/v1", model="m", max_retries=0)
    c = LLMClient(p, http_client=httpx2.Client(transport=httpx2.MockTransport(down)))
    with pytest.raises(LLMUnavailableError, match="indisponível"):
        c.chat([{"role": "user", "content": "oi"}])
    assert not c.ping()


def test_server_error_raises_unavailable():
    with pytest.raises(LLMUnavailableError):
        client(FakeServer(status=500)).chat([{"role": "user", "content": "oi"}])


# ============================================================================ CLI


def test_cli_llm_list(capsys):
    from emporio.cli import main

    assert main(["llm", "list"]) == 0
    out = capsys.readouterr().out
    assert "* bonsai" in out
    assert "referência" in out


def test_cli_llm_check_without_key(monkeypatch, capsys):
    from emporio.cli import main

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["llm", "check", "--profile", "anthropic"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
