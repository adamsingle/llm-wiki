"""
providers.py — LLM provider clients for the Wiki Agent.

Supports Ollama (local), Anthropic (Claude), OpenAI, and Google Gemini.
All providers expose the same interface so agent.py doesn't need to care
which one is in use.

Each provider normalises its tool-calling format to/from the common
Ollama/OpenAI shape used internally:

  Tool definition:
    {"type": "function", "function": {"name": ..., "description": ...,
     "parameters": {"type": "object", "properties": {...}, "required": [...]}}}

  Tool call in response:
    {"function": {"name": ..., "arguments": {...}}}

  Tool result in messages:
    {"role": "tool", "content": "<result string>"}
"""

import json
import os
import requests
from abc import ABC, abstractmethod
from typing import Optional


# ─── Base class ───────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """Common interface all providers must implement."""

    @abstractmethod
    def chat(self, messages: list, tools: list = None) -> dict:
        """
        Send a chat request. Returns a normalised response dict:
          {
            "message": {
              "role": "assistant",
              "content": "<text or empty string>",
              "tool_calls": [   # may be absent or empty
                {"function": {"name": "...", "arguments": {...}}}
              ]
            }
          }
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider is reachable and configured."""

    @abstractmethod
    def list_models(self) -> list:
        """Return available model names (best-effort)."""


# ─── Ollama ───────────────────────────────────────────────────────────────────

class OllamaProvider(LLMProvider):
    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def chat(self, messages: list, tools: list = None) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        resp = requests.post(
            f"{self.base_url}/api/chat", json=payload, timeout=300
        )
        resp.raise_for_status()
        return resp.json()

    def is_available(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/api/tags", timeout=5).status_code == 200
        except Exception:
            return False

    def list_models(self) -> list:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=10)
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []


# ─── Anthropic (Claude) ───────────────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    # Models available as of mid-2025
    KNOWN_MODELS = [
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    ]

    def __init__(self, model: str, api_key: str = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    # ── Format conversion ─────────────────────────────────────────────────────

    def _convert_tools(self, tools: list) -> list:
        """Convert OpenAI-style tool defs to Anthropic format."""
        converted = []
        for t in tools:
            fn = t.get("function", {})
            converted.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {
                    "type": "object", "properties": {}, "required": []
                }),
            })
        return converted

    def _convert_messages(self, messages: list) -> tuple:
        """
        Split messages into (system_prompt, user_messages).
        Anthropic puts the system prompt separately and uses a different
        format for tool results.
        """
        system = ""
        converted = []

        for msg in messages:
            role = msg.get("role")

            if role == "system":
                system = msg.get("content", "")
                continue

            if role == "tool":
                # Tool results must be wrapped as user messages in Anthropic format
                # Find the most recent assistant message with tool_calls to get the ID
                tool_use_id = self._last_tool_use_id(converted)
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": msg.get("content", ""),
                    }]
                })
                continue

            if role == "assistant":
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls", [])

                if tool_calls:
                    # Convert tool_calls to Anthropic content blocks
                    blocks = []
                    if content:
                        blocks.append({"type": "text", "text": content})
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        args = fn.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", f"toolu_{len(converted)}"),
                            "name": fn.get("name", ""),
                            "input": args,
                        })
                    converted.append({"role": "assistant", "content": blocks})
                else:
                    converted.append({"role": "assistant", "content": content or " "})
                continue

            if role == "user":
                content = msg.get("content", "")
                converted.append({"role": "user", "content": content})

        return system, converted

    def _last_tool_use_id(self, messages: list) -> str:
        """Find the ID of the most recent tool_use block in converted messages."""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in reversed(content):
                        if block.get("type") == "tool_use":
                            return block.get("id", "toolu_0")
        return "toolu_0"

    def _normalise_response(self, data: dict) -> dict:
        """Convert Anthropic response to the normalised internal format."""
        content_blocks = data.get("content", [])
        text_parts = []
        tool_calls = []

        for i, block in enumerate(content_blocks):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", f"toolu_{i}"),
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": block.get("input", {}),
                    }
                })

        message = {
            "role": "assistant",
            "content": "\n".join(text_parts),
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {"message": message}

    # ── Public interface ──────────────────────────────────────────────────────

    def chat(self, messages: list, tools: list = None) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "Anthropic API key not set. "
                "Set ANTHROPIC_API_KEY environment variable or add api_key to config.yaml."
            )

        system, converted_messages = self._convert_messages(messages)

        # Anthropic requires alternating user/assistant turns — merge consecutive
        # same-role messages if they occur (can happen with multiple tool results)
        merged = []
        for msg in converted_messages:
            if merged and merged[-1]["role"] == msg["role"] == "user":
                # Merge by combining content
                prev = merged[-1]["content"]
                curr = msg["content"]
                if isinstance(prev, list) and isinstance(curr, list):
                    merged[-1]["content"] = prev + curr
                elif isinstance(prev, str) and isinstance(curr, str):
                    merged[-1]["content"] = prev + "\n" + curr
                else:
                    merged.append(msg)
            else:
                merged.append(msg)

        payload = {
            "model": self.model,
            "max_tokens": 8096,
            "messages": merged,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self._convert_tools(tools)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=300)

        if not resp.ok:
            raise RuntimeError(
                f"Anthropic API error {resp.status_code}: {resp.text[:400]}"
            )

        return self._normalise_response(resp.json())

    def is_available(self) -> bool:
        return bool(self.api_key)

    def list_models(self) -> list:
        return self.KNOWN_MODELS


# ─── OpenAI ───────────────────────────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    API_URL = "https://api.openai.com/v1/chat/completions"
    KNOWN_MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]

    def __init__(self, model: str, api_key: str = None, base_url: str = None):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.api_url = (base_url or self.API_URL).rstrip("/")
        if not self.api_url.endswith("/chat/completions"):
            self.api_url = self.api_url.rstrip("/") + "/chat/completions"

    def _normalise_response(self, data: dict) -> dict:
        """Convert OpenAI response to normalised internal format."""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})

        tool_calls_raw = message.get("tool_calls", [])
        tool_calls = []
        for tc in tool_calls_raw:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tool_calls.append({
                "id": tc.get("id", ""),
                "function": {"name": fn.get("name", ""), "arguments": args}
            })

        result = {
            "role": "assistant",
            "content": message.get("content", "") or "",
        }
        if tool_calls:
            result["tool_calls"] = tool_calls

        return {"message": result}

    def chat(self, messages: list, tools: list = None) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "OpenAI API key not set. "
                "Set OPENAI_API_KEY environment variable or add api_key to config.yaml."
            )

        def _fix_messages(msgs):
            fixed = []
            for msg in msgs:
                msg = dict(msg)
                if msg.get("tool_calls"):
                    tcs = []
                    for tc in msg["tool_calls"]:
                        tc = dict(tc)
                        fn = dict(tc.get("function", {}))
                        if isinstance(fn.get("arguments"), dict):
                            fn["arguments"] = json.dumps(fn["arguments"])
                        tc["function"] = fn
                        tcs.append(tc)
                    msg["tool_calls"] = tcs
                fixed.append(msg)
            return fixed

        payload = {"model": self.model, "messages": _fix_messages(messages)}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        resp = requests.post(self.api_url, json=payload, headers=headers, timeout=300)
        if not resp.ok:
            raise RuntimeError(
                f"OpenAI API error {resp.status_code}: {resp.text[:400]}"
            )
        return self._normalise_response(resp.json())

    def is_available(self) -> bool:
        return bool(self.api_key)

    def list_models(self) -> list:
        return self.KNOWN_MODELS


# ─── Google Gemini ────────────────────────────────────────────────────────────

class GeminiProvider(LLMProvider):
    OPENAI_COMPAT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Tried in order if the primary model fails with 429/503
    FALLBACK_MODELS = [
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
    ]

    def __init__(self, model: str, api_key: str = None):
        self.model = model
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")

    def _make_delegate(self, model: str) -> "OpenAIProvider":
        return OpenAIProvider(
            model=model,
            api_key=self.api_key,
            base_url=self.OPENAI_COMPAT_URL,
        )

    def chat(self, messages: list, tools: list = None) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "Google API key not set. "
                "Set GOOGLE_API_KEY or add api_key to config.yaml."
            )

        # Build the list of models to try: configured model first, then fallbacks
        models_to_try = [self.model] + [
            m for m in self.FALLBACK_MODELS if m != self.model
        ]

        last_error = None
        for model in models_to_try:
            try:
                result = self._make_delegate(model).chat(messages, tools)
                if model != self.model:
                    print(f"  ℹ️  Fell back to model: {model}")
                return result
            except RuntimeError as e:
                msg = str(e)
                if "429" in msg or "503" in msg or "UNAVAILABLE" in msg or "quota" in msg.lower():
                    print(f"  ⚠️  {model} unavailable, trying next model...")
                    last_error = e
                    continue
                # Non-recoverable error — don't try fallbacks
                raise

        # All models exhausted
        raise RuntimeError(
            f"All Gemini models unavailable. Last error:\n{last_error}\n\n"
            "Try again in a few minutes, or switch to Ollama in config.yaml."
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def list_models(self) -> list:
        return [self.model] + self.FALLBACK_MODELS


# ─── Factory ──────────────────────────────────────────────────────────────────

def create_provider(config: dict) -> LLMProvider:
    """
    Build the right provider from config.yaml.

    config.yaml examples:

      # Ollama (local, default)
      ollama:
        model: qwen2.5:14b
        base_url: http://localhost:11434

      # Anthropic Claude
      anthropic:
        model: claude-sonnet-4-5
        api_key: sk-ant-...   # or set ANTHROPIC_API_KEY env var

      # OpenAI
      openai:
        model: gpt-4o
        api_key: sk-...       # or set OPENAI_API_KEY env var

      # Gemini
      gemini:
        model: gemini-2.0-flash
        api_key: AIza...      # or set GOOGLE_API_KEY env var

    The first matching key found is used. If none match, falls back to Ollama.
    """
    if "anthropic" in config:
        c = config["anthropic"]
        return AnthropicProvider(
            model=c.get("model", "claude-sonnet-4-5"),
            api_key=c.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", ""),
        )

    if "openai" in config:
        c = config["openai"]
        return OpenAIProvider(
            model=c.get("model", "gpt-4o"),
            api_key=c.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
            base_url=c.get("base_url"),
        )

    if "gemini" in config:
        c = config["gemini"]
        return GeminiProvider(
            model=c.get("model", "gemini-2.0-flash"),
            api_key=c.get("api_key") or os.environ.get("GOOGLE_API_KEY", ""),
        )

    # Default: Ollama
    c = config.get("ollama", {})
    return OllamaProvider(
        model=c.get("model", "qwen2.5:14b"),
        base_url=c.get("base_url", "http://localhost:11434"),
    )