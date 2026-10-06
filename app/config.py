"""Settings in one place. Everything reads the environment at call time, so tests can override it."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from boardbot/.env into the environment, so settings live in one file instead of
    terminal variables. Real environment variables win; empty values and '...' placeholders are ignored."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value and not value.endswith("...") and key.strip() not in os.environ:
            os.environ[key.strip()] = value


load_dotenv()


def data_dir() -> Path:
    path = Path(os.environ.get("BOARDBOT_DATA_DIR", ROOT / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return Path(os.environ.get("BOARDBOT_DB", data_dir() / "boardbot.db"))


def default_mode() -> str:
    """"fast" (one model call + code repair: seconds) or "agent" (adds a tool-calling loop: slower, more tokens)."""
    return os.environ.get("BOARDBOT_MODE", "fast").lower()


def timezone_name() -> str:
    """Time zone for calendar events (used by Google Calendar)."""
    return os.environ.get("BOARDBOT_TIMEZONE", "Asia/Kolkata")


def today() -> date:
    """The app's idea of 'today'. Fixed by default so the sample meetings ('Friday', 'next Wed')
    always resolve to the same dates. Set BOARDBOT_TODAY=real to use the system clock."""
    value = os.environ.get("BOARDBOT_TODAY", "2026-10-06")
    return date.today() if value == "real" else date.fromisoformat(value)


# provider -> (API base URL, default model, name of the environment variable holding its key)
# All of these speak the same "OpenAI-style" chat API, so one class (OpenAICompatLLM) serves them all.
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash", "GEMINI_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b", "CEREBRAS_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
}


@dataclass(frozen=True)
class Settings:
    """Which model the agent's BRAIN uses. All overridable in .env."""

    provider: str = "groq"  # groq | gemini | cerebras | openrouter (or any OpenAI-style API via BOARDBOT_BASE_URL)
    model: str = "openai/gpt-oss-120b"
    base_url: str = "https://api.groq.com/openai/v1"
    api_key: str = field(default="", repr=False)  # never printed
    temperature: float = 0.2  # low = more predictable, which matters for tool calling
    reasoning_effort: str | None = None  # for reasoning models such as gpt-oss: low | medium | high
    fallback_model: str | None = None  # tried when the main model's daily free allowance is used up

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.environ.get("BOARDBOT_PROVIDER", "groq").lower()
        temperature = float(os.environ.get("BOARDBOT_TEMPERATURE", cls.temperature))
        base_url, default_model, key_var = PROVIDERS.get(provider, (os.environ.get("BOARDBOT_BASE_URL", ""), "", "BOARDBOT_API_KEY"))
        model = os.environ.get("BOARDBOT_MODEL", default_model)
        effort = os.environ.get("BOARDBOT_REASONING_EFFORT") or ("low" if model.startswith("openai/gpt-oss") else None)
        fallback = os.environ.get("BOARDBOT_FALLBACK_MODEL") or ("openai/gpt-oss-20b" if provider == "groq" and model == "openai/gpt-oss-120b" else None)
        return cls(
            provider=provider,
            fallback_model=fallback,
            reasoning_effort=effort,
            model=model,
            base_url=os.environ.get("BOARDBOT_BASE_URL", base_url),
            api_key=os.environ.get(key_var, "") or os.environ.get("BOARDBOT_API_KEY", ""),
            temperature=temperature,
        )
