"""The model layer: the ONLY file that knows how to talk to a language model.

The rest of the app (agent loop, planner, tools) just calls `llm.chat(messages, tools=..., schema=...)` and gets a
`Reply` back. That means you can swap the model without touching the agent: to add a provider, write one more class with
the same `chat()` and `problem()` methods (for example Amazon Bedrock) and return it from `make_llm`.

    OpenAICompatLLM   any hosted provider that speaks the "OpenAI-style" chat API: Groq (default, free, fast), OpenRouter,
                      Google Gemini, Cerebras ... Only the transcript text is sent.

The agent keeps ONE neutral message format (see agent.py). The class below translates it to the provider's wire format.
We call the API over plain HTTP (httpx) so you can see exactly what goes over the wire.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import httpx


class LLMUnavailable(RuntimeError):
    """Raised with a plain-English fix when the model can't be reached or refuses us."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:10])


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class LLM(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
    ) -> Reply: ...

    async def problem(self) -> str | None:
        """None if the model is ready to use; otherwise a one-line explanation of what to fix."""
        ...


def _as_dict(arguments: Any) -> dict[str, Any]:
    """Models return tool arguments as a JSON *string* (OpenAI-style) or sometimes already as a dict. Accept both."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _tool_specs(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Our neutral tool description -> the 'function' format both providers understand."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]


# ------------------------------------------------------------------ hosted, OpenAI-style (Groq and friends)


MAX_WAIT_SECONDS = 45.0  # longer than this is a daily limit, not a per-minute one: tell the human instead of hanging


def _rate_limit_wait(response: httpx.Response, attempt: int) -> float:
    """How long the provider says to wait: the retry-after header, else 'try again in 7m12.5s' in the message, else back off."""
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        pass
    if m := re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", response.text):
        h, mins, secs = (float(x) if x else 0.0 for x in m.groups())
        if h or mins or secs:
            return h * 3600 + mins * 60 + secs + 0.5
    return 2 ** attempt * 2.0


def _rate_limit_message(provider: str, response: httpx.Response, wait: float) -> str:
    try:
        detail = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        detail = response.text
    when = f"about {round(wait / 60)} min" if wait >= 90 else f"about {round(wait)} s"
    return (f"The free {provider} limit was reached; it clears in {when}. ({detail[:200]}) "
            "Wait, or set BOARDBOT_MODEL to a smaller model with a bigger free allowance (e.g. openai/gpt-oss-20b).")


class OpenAICompatLLM:
    """Talks to any OpenAI-style `/chat/completions` endpoint. Default provider: Groq's free tier.

    schema   these APIs have no universal 'force this JSON schema' switch, so we (1) turn on JSON mode, (2) put the schema
             in the prompt, and (3) let the planner validate and retry. For a big model that is dependable.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.2,
        timeout: float = 120.0,
        max_retries: int = 3,
        provider: str = "groq",
        reasoning_effort: str | None = None,
        fallback_model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.model, self.base_url, self.provider = model, base_url.rstrip("/"), provider
        self.temperature, self.max_retries, self._sleep = temperature, max_retries, sleep
        self.fallback_model = fallback_model  # used when the main model's DAILY free allowance is gone (each model has its own)
        self.reasoning_effort = reasoning_effort  # 'low' | 'medium' | 'high' for reasoning models (gpt-oss); None = don't send
        self._key = api_key
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    # -- translating our neutral messages into the provider's format
    @staticmethod
    def _to_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                calls = [
                    {"id": c.get("id") or f"call_{i}", "type": "function",
                     "function": {"name": c["function"]["name"], "arguments": json.dumps(c["function"]["arguments"])}}
                    for i, c in enumerate(m["tool_calls"])
                ]
                wire.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
            elif m["role"] == "tool":
                wire.append({"role": "tool", "tool_call_id": m.get("tool_call_id") or "", "content": m["content"]})
            else:
                wire.append({"role": m["role"], "content": m["content"]})
        return wire

    def _key_problem(self) -> str | None:
        if not self._key:
            return f"No API key found for {self.provider}. Create a free one (see SETUP.md) and put it in the .env file."
        return None

    async def chat(self, messages, tools=None, schema=None) -> Reply:
        if problem := self._key_problem():
            raise LLMUnavailable(problem)
        wire = self._to_wire(messages)
        payload: dict[str, Any] = {"model": self.model, "messages": wire, "temperature": self.temperature}
        if tools:
            payload["tools"] = _tool_specs(tools)
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort  # less 'thinking' before answering = faster and fewer tokens
        if schema:
            payload["response_format"] = {"type": "json_object"}
            instruction = "\n\nReply with ONLY one JSON object (no prose, no code fences) that matches this JSON schema:\n" + json.dumps(schema)
            last = wire[-1]
            if last["role"] == "user":
                last["content"] = f"{last['content']}{instruction}"
            else:
                wire.append({"role": "user", "content": instruction.strip()})

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._http.post(f"{self.base_url}/chat/completions", json=payload, headers={"Authorization": f"Bearer {self._key}"})
            except httpx.ConnectError:
                raise LLMUnavailable(f"Could not reach {self.base_url}. Check your internet connection.") from None
            except httpx.TimeoutException:
                raise LLMUnavailable("The model API took too long to answer. Try again.") from None

            if response.status_code in (401, 403):
                raise LLMUnavailable(f"The {self.provider} API key was rejected. Check the key in your .env file.")
            if response.status_code == 429:  # free tiers limit tokens/requests per minute (and per day)
                wait = _rate_limit_wait(response, attempt)
                if wait <= MAX_WAIT_SECONDS and attempt < self.max_retries:  # a per-minute limit clears soon: wait and retry
                    await self._sleep(wait)
                    continue
                if self.fallback_model and payload["model"] != self.fallback_model:  # long wait: switch to the other model, keep going
                    payload["model"] = self.fallback_model
                    continue
                raise LLMUnavailable(_rate_limit_message(self.provider, response, wait))
            if response.status_code >= 500 and attempt < self.max_retries:
                await self._sleep(2.0 * (attempt + 1))
                continue
            if response.status_code == 400 and "tool_use_failed" in response.text:
                return Reply("", [], 0, 0)  # the model wrote a malformed tool call; treat as 'no tool call' and let the loop go on
            if response.status_code == 400 and "json_validate_failed" in response.text:
                # JSON mode sometimes rejects the model's own output. The error carries what it tried to write: hand that to the
                # planner, whose validate-and-retry loop will accept it if it parses or ask the model to fix it. No attempt left
                # to salvage? Retry the call once more; it usually works the second time.
                try:
                    attempted = response.json()["error"].get("failed_generation") or ""
                except (ValueError, KeyError, AttributeError):
                    attempted = ""
                if attempted:
                    return Reply(attempted, [], 0, 0)
                if attempt < self.max_retries:
                    await self._sleep(1.0)
                    continue
                return Reply("", [], 0, 0)
            if response.status_code >= 400:
                raise LLMUnavailable(f"{self.provider} error {response.status_code}: {response.text[:300]}")
            break

        data = response.json()
        message = data["choices"][0]["message"]
        calls = [
            ToolCall(c["function"]["name"], _as_dict(c["function"].get("arguments")), c.get("id") or "call_" + uuid.uuid4().hex[:10])
            for c in message.get("tool_calls") or []
        ]
        usage = data.get("usage", {})
        return Reply(message.get("content") or "", calls, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    async def problem(self) -> str | None:
        if problem := self._key_problem():
            return problem
        try:
            r = await self._http.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self._key}"}, timeout=8.0)
        except (httpx.ConnectError, httpx.TimeoutException):
            return f"Could not reach {self.base_url}. Check your internet connection."
        if r.status_code in (401, 403):
            return f"The {self.provider} API key was rejected. Check the key in your .env file."
        if r.status_code == 200:
            ids = [m.get("id") for m in r.json().get("data", [])]
            if ids and self.model not in ids:
                return f"The model '{self.model}' is not available to your key. Pick one of: {', '.join(sorted(i for i in ids if i)[:8])} (set BOARDBOT_MODEL)."
        return None


def make_llm(settings) -> LLM:
    """Build the model object for the configured provider. Everything else in the app calls this, never a class directly."""
    return OpenAICompatLLM(settings.model, settings.base_url, settings.api_key, settings.temperature, provider=settings.provider,
                           reasoning_effort=settings.reasoning_effort, fallback_model=settings.fallback_model)
