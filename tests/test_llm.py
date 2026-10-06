"""The model layer, tested against a mocked HTTP API (no key, no internet, no cost)."""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.llm import LLMUnavailable, OpenAICompatLLM, ToolCall, make_llm


def run(coro):
    return asyncio.run(coro)


def llm_with(handler, key="gsk_test", **kw) -> OpenAICompatLLM:
    async def no_sleep(seconds):
        pass

    return OpenAICompatLLM("llama-3.3-70b-versatile", "https://api.example.com/v1", key, transport=httpx.MockTransport(handler), sleep=no_sleep, **kw)


def ok(message: dict, usage=(11, 7)) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": message}], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})


TOOLS = [{"name": "find_person", "description": "d", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}]


def test_request_uses_the_openai_style_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return ok({"content": "hi"})

    reply = run(llm_with(handler).chat([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], tools=TOOLS))
    assert seen["url"] == "https://api.example.com/v1/chat/completions" and seen["auth"] == "Bearer gsk_test"
    assert seen["body"]["tools"][0] == {"type": "function", "function": {"name": "find_person", "description": "d", "parameters": TOOLS[0]["input_schema"]}}
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ("hi", 11, 7)


def test_tool_calls_round_trip_with_ids_and_string_arguments():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return ok({"content": None, "tool_calls": [{"id": "call_9", "type": "function", "function": {"name": "find_person", "arguments": '{"query": "Rahul"}'}}]})
        return ok({"content": "done"})

    llm = llm_with(handler)
    first = run(llm.chat([{"role": "user", "content": "u"}], tools=TOOLS))
    assert first.tool_calls == [ToolCall("find_person", {"query": "Rahul"}, "call_9")]  # JSON string parsed into a dict

    history = [{"role": "user", "content": "u"},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "call_9", "function": {"name": "find_person", "arguments": {"query": "Rahul"}}}]},
               {"role": "tool", "tool_name": "find_person", "tool_call_id": "call_9", "content": "{...}"}]
    run(llm.chat(history, tools=TOOLS))
    sent = bodies[1]["messages"]
    assert sent[1]["tool_calls"][0] == {"id": "call_9", "type": "function", "function": {"name": "find_person", "arguments": '{"query": "Rahul"}'}}
    assert sent[1]["content"] is None and sent[2] == {"role": "tool", "tool_call_id": "call_9", "content": "{...}"}  # id links result to call


def test_schema_turns_on_json_mode_and_goes_into_the_prompt():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return ok({"content": '{"a": 1}'})

    run(llm_with(handler).chat([{"role": "user", "content": "write it"}], schema={"type": "object", "properties": {"a": {"type": "integer"}}}))
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert "JSON schema" in seen["body"]["messages"][-1]["content"] and '"a"' in seen["body"]["messages"][-1]["content"]


def test_rate_limit_waits_and_retries():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "1"}, text="slow down") if len(calls) < 3 else ok({"content": "finally"})

    assert run(llm_with(handler).chat([{"role": "user", "content": "u"}])).text == "finally" and len(calls) == 3


def test_persistent_rate_limit_is_explained():
    with pytest.raises(LLMUnavailable, match="free groq limit was reached"):
        run(llm_with(lambda r: httpx.Response(429, text="tokens per day"), provider="groq").chat([{"role": "user", "content": "u"}]))


def test_bad_key_and_missing_key_are_explained():
    with pytest.raises(LLMUnavailable, match="key was rejected"):
        run(llm_with(lambda r: httpx.Response(401)).chat([{"role": "user", "content": "u"}]))
    with pytest.raises(LLMUnavailable, match="No API key found"):
        run(llm_with(lambda r: ok({"content": "x"}), key="").chat([{"role": "user", "content": "u"}]))


def test_malformed_tool_call_does_not_crash_the_run():
    reply = run(llm_with(lambda r: httpx.Response(400, json={"error": {"code": "tool_use_failed"}})).chat([{"role": "user", "content": "u"}], tools=TOOLS))
    assert reply.text == "" and reply.tool_calls == []


def test_problem_reports_what_to_fix():
    assert run(llm_with(lambda r: ok({}), key="").problem()).startswith("No API key found")
    assert "rejected" in run(llm_with(lambda r: httpx.Response(401)).problem())
    models = httpx.Response(200, json={"data": [{"id": "llama-3.1-8b-instant"}, {"id": "other"}]})
    assert "not available to your key" in run(llm_with(lambda r: models).problem())
    assert run(llm_with(lambda r: httpx.Response(200, json={"data": [{"id": "llama-3.3-70b-versatile"}]})).problem()) is None


def test_settings_and_factory(monkeypatch):
    for k in ("BOARDBOT_PROVIDER", "BOARDBOT_MODEL", "BOARDBOT_BASE_URL", "BOARDBOT_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_abc")
    s = Settings.from_env()
    assert (s.provider, s.model, s.api_key) == ("groq", "openai/gpt-oss-120b", "gsk_abc") and "gsk_abc" not in repr(s)  # key never printed
    assert isinstance(make_llm(s), OpenAICompatLLM)

    monkeypatch.setenv("BOARDBOT_PROVIDER", "mycloud")
    monkeypatch.setenv("BOARDBOT_BASE_URL", "https://x.test/v1")
    monkeypatch.setenv("BOARDBOT_API_KEY", "k")
    monkeypatch.setenv("BOARDBOT_MODEL", "m")
    custom = Settings.from_env()
    assert (custom.base_url, custom.api_key, custom.model) == ("https://x.test/v1", "k", "m")



def test_reasoning_effort_is_sent_only_when_set(monkeypatch):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return ok({"content": "hi"})

    run(llm_with(handler, reasoning_effort="low").chat([{"role": "user", "content": "u"}]))
    run(llm_with(handler).chat([{"role": "user", "content": "u"}]))
    assert bodies[0]["reasoning_effort"] == "low" and "reasoning_effort" not in bodies[1]

    for k in ("BOARDBOT_PROVIDER", "BOARDBOT_MODEL", "BOARDBOT_REASONING_EFFORT"):
        monkeypatch.delenv(k, raising=False)
    assert Settings.from_env().reasoning_effort == "low"  # default model is a gpt-oss reasoning model
    monkeypatch.setenv("BOARDBOT_MODEL", "qwen/qwen3.8-27b")
    assert Settings.from_env().reasoning_effort is None


def test_json_mode_rejection_is_recovered_not_fatal():
    """Groq sometimes answers 400 'json_validate_failed' and includes what the model tried to write."""
    err = {"error": {"code": "json_validate_failed", "message": "Failed to generate JSON", "failed_generation": '{"summary": "ok"}'}}
    reply = run(llm_with(lambda r: httpx.Response(400, json=err)).chat([{"role": "user", "content": "u"}], schema={"type": "object"}))
    assert reply.text == '{"summary": "ok"}'  # the planner will validate it (and ask for a fix if it is broken)

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        bare = {"error": {"code": "json_validate_failed", "message": "Failed to generate JSON"}}
        return httpx.Response(400, json=bare) if len(calls) == 1 else ok({"content": '{"a": 1}'})

    assert run(llm_with(handler).chat([{"role": "user", "content": "u"}], schema={"type": "object"})).text == '{"a": 1}' and len(calls) == 2


def test_per_minute_limit_waits_the_time_the_provider_asks():
    calls, slept = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        text = '{"error":{"message":"Rate limit reached ... on tokens per minute (TPM). Please try again in 7.5s."}}'
        return httpx.Response(429, text=text) if len(calls) < 2 else ok({"content": "ok now"})

    llm = llm_with(handler)
    llm._sleep = lambda s: slept.append(s) or asyncio.sleep(0)
    assert run(llm.chat([{"role": "user", "content": "u"}])).text == "ok now" and slept == [8.0]


def test_daily_limit_is_reported_with_the_wait_instead_of_hanging():
    text = '{"error":{"message":"Rate limit reached on tokens per day (TPD). Please try again in 21m30s."}}'
    with pytest.raises(LLMUnavailable, match=r"clears in about 22 min"):
        run(llm_with(lambda r: httpx.Response(429, text=text)).chat([{"role": "user", "content": "u"}]))


def test_daily_limit_falls_back_to_the_smaller_model():
    models = []

    def handler(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        if models[-1] == "llama-3.3-70b-versatile":
            return httpx.Response(429, text='{"error":{"message":"tokens per day (TPD). Please try again in 21m30s."}}')
        return ok({"content": "from the small one"})

    reply = run(llm_with(handler, fallback_model="small-model").chat([{"role": "user", "content": "u"}]))
    assert reply.text == "from the small one" and models == ["llama-3.3-70b-versatile", "small-model"]
