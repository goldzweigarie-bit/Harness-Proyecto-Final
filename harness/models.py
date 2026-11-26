"""Model providers. Standard library only (urllib), so the harness has no dependencies.

Each model exposes: query(system: str, messages: list[dict]) -> (text, usage)
where usage = {"input_tokens": int, "output_tokens": int}.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

RETRY_STATUS = {429, 500, 502, 503, 529}


def _post_json(url: str, headers: dict, body: dict, retries: int = 4, timeout: int = 120) -> dict:
    """POST JSON, retrying on rate limits and server errors. `timeout` is per request, in seconds."""
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in RETRY_STATUS and attempt < retries:
                wait = float(e.headers.get("retry-after") or 2 ** attempt)
                time.sleep(min(wait, 30))
                continue
            raise RuntimeError(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:500]}") from None


class AnthropicModel:
    def __init__(self, model: str = "claude-sonnet-5-5", max_tokens: int = 4096, api_key: str | None = None):
        self.name = model
        self.max_tokens = max_tokens
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]

    def query(self, system, messages):
        resp = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {"model": self.name, "max_tokens": self.max_tokens, "system": system, "messages": messages},
        )
        text = "".join(b.get("text", "") for b in resp["content"] if b["type"] == "text")
        u = resp.get("usage", {})
        return text, {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}


class OpenAICompatModel:
    """Any /chat/completions endpoint: OpenAI, OpenRouter, Ollama, vLLM..."""

    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None, max_tokens: int = 4096,
                 request_timeout: int = 120):
        self.name = model
        self.max_tokens = max_tokens
        self.request_timeout = request_timeout
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "none")

    def query(self, system, messages):
        resp = _post_json(
            f"{self.base_url}/chat/completions",
            {"authorization": f"Bearer {self.api_key}"},
            {"model": self.name, "max_tokens": self.max_tokens,
             "messages": [{"role": "system", "content": system}, *messages]},
            timeout=self.request_timeout,
        )
        u = resp.get("usage", {})
        return resp["choices"][0]["message"]["content"] or "", {
            "input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}


class ScriptedModel:
    """Replays canned replies. For tests and for checking the sandbox without spending tokens."""

    def __init__(self, replies: list[str]):
        self.name = "scripted"
        self.replies = list(replies)

    def query(self, system, messages):
        if not self.replies:
            return "```bash\nsubmit\n```", {}
        return self.replies.pop(0), {"input_tokens": 0, "output_tokens": 0}


def get_model(provider: str, model: str | None):
    if provider == "anthropic":
        return AnthropicModel(model or "claude-sonnet-5-5")
    if provider == "openai":
        return OpenAICompatModel(model or "gpt-4.1-mini")
    if provider == "ollama":
        # Local model through Ollama's OpenAI-compatible endpoint: free, offline, no key.
        # Local inference on a laptop is slow, so each request may take up to 10 minutes.
        return OpenAICompatModel(
            model or os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama", request_timeout=600,
        )
    raise ValueError(f"unknown provider {provider!r} (use anthropic, openai or ollama)")
