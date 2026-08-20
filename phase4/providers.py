"""
Phase 4 · LLM provider abstraction.

Supports multiple LLM backends behind a unified interface so the answering
layer doesn't care which model is running.

Supported providers:
  anthropic  — Claude (Sonnet 4, Opus 4, Haiku)
  openai     — GPT-4o, GPT-4o-mini, o4-mini, etc.
  google     — Gemini 2.5 Pro, Gemini 2.0 Flash, etc.

Each provider translates between its native tool-use format and a common
internal representation.
"""

from __future__ import annotations

import os
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from phase4 import prompts


# ── common types ──────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """A single tool invocation from the model."""
    id:    str
    name:  str
    input: dict


@dataclass
class LLMResponse:
    """Standardised response from any provider."""
    text:           str                     # concatenated text content
    tool_calls:     list[ToolCall]          # tool invocations (if any)
    wants_tool_use: bool                    # True → needs tool results before continuing
    input_tokens:   int
    output_tokens:  int
    raw:            object = None           # provider-specific response object


# ── base class ────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send messages and return a standardised response."""

    @abstractmethod
    def format_tool_result(self, tool_call: ToolCall, result: str) -> dict:
        """Format a tool result message for the next request."""

    @abstractmethod
    def format_assistant_msg(self, response: LLMResponse) -> dict:
        """Format the assistant's response as a message for conversation history."""


# ── Anthropic (Claude) ───────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    name = "anthropic"

    DEFAULT_MODELS = {
        "sonnet": "claude-sonnet-4-20250514",
        "opus":   "claude-opus-4-20250514",
        "haiku":  "claude-haiku-4-5-20251001",
    }

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set.\n  set ANTHROPIC_API_KEY=sk-ant-..."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = self.DEFAULT_MODELS.get(model, model)

    def chat(self, messages: list[dict], max_tokens: int = 4096) -> LLMResponse:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=prompts.SYSTEM_PROMPT,
            tools=prompts.TOOL_DEFINITIONS,
            messages=messages,
        )

        text = ""
        tool_calls = []
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            wants_tool_use=(response.stop_reason == "tool_use"),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            raw=response,
        )

    def format_tool_result(self, tool_call: ToolCall, result: str) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": tool_call.id,
            "content": result,
        }

    def format_assistant_msg(self, response: LLMResponse) -> dict:
        return {"role": "assistant", "content": response.raw.content}


# ── OpenAI (GPT) ─────────────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    name = "openai"

    DEFAULT_MODELS = {
        "gpt4o":      "gpt-4o",
        "gpt4o-mini": "gpt-4o-mini",
        "o4-mini":    "o4-mini",
        "gpt4":       "gpt-4-turbo",
    }

    def __init__(self, model: str = "gpt-4o-mini"):
        # pyrefly: ignore [missing-import]
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set.\n  set OPENAI_API_KEY=sk-..."
            )
        self.client = OpenAI(api_key=api_key)
        self.model = self.DEFAULT_MODELS.get(model, model)

        # Convert Anthropic tool definitions to OpenAI format
        self._tools = []
        for tool_def in prompts.TOOL_DEFINITIONS:
            self._tools.append({
                "type": "function",
                "function": {
                    "name": tool_def["name"],
                    "description": tool_def["description"],
                    "parameters": tool_def["input_schema"],
                },
            })

    def chat(self, messages: list[dict], max_tokens: int = 4096) -> LLMResponse:
        # Prepend system message if not already present
        oai_messages = []
        has_system = any(m.get("role") == "system" for m in messages)
        if not has_system:
            oai_messages.append({"role": "system", "content": prompts.SYSTEM_PROMPT})
        oai_messages.extend(messages)

        kwargs = dict(
            model=self.model,
            messages=oai_messages,
            tools=self._tools,
            max_tokens=max_tokens,
        )

        # o-series models don't support max_tokens the same way
        if self.model.startswith("o"):
            kwargs.pop("max_tokens", None)

        response = self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        msg = choice.message

        text = msg.content or ""
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {"raw": tc.function.arguments}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=args,
                ))

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            wants_tool_use=(choice.finish_reason == "tool_calls"),
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            raw=response,
        )

    def format_tool_result(self, tool_call: ToolCall, result: str) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        }

    def format_assistant_msg(self, response: LLMResponse) -> dict:
        msg = response.raw.choices[0].message
        d = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        return d


# ── Google (Gemini) ───────────────────────────────────────────────────

class GoogleProvider(LLMProvider):
    name = "google"

    DEFAULT_MODELS = {
        "flash":  "gemini-2.0-flash",
        "pro":    "gemini-2.5-pro",
    }

    def __init__(self, model: str = "gemini-2.0-flash"):
        from google import genai
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY not set.\n  set GOOGLE_API_KEY=AI..."
            )
        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.model = self.DEFAULT_MODELS.get(model, model)

        # Build Gemini tool declarations
        self._tool_declarations = []
        for tool_def in prompts.TOOL_DEFINITIONS:
            schema = dict(tool_def["input_schema"])
            # Gemini doesn't want 'required' at top level in some versions
            self._tool_declarations.append(
                genai.types.FunctionDeclaration(
                    name=tool_def["name"],
                    description=tool_def["description"],
                    parameters=schema,
                )
            )

    def chat(self, messages: list[dict], max_tokens: int = 4096) -> LLMResponse:
        from google.genai import types as gtypes

        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "system":
                continue  # handled via system_instruction
            elif role == "user":
                if isinstance(content, str):
                    contents.append(gtypes.Content(
                        role="user",
                        parts=[gtypes.Part(text=content)],
                    ))
                elif isinstance(content, list):
                    # Tool results
                    parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("function_response"):
                            parts.append(gtypes.Part(
                                function_response=item["function_response"]
                            ))
                        elif isinstance(item, dict) and "text" in item:
                            parts.append(gtypes.Part(text=item["text"]))
                    if parts:
                        contents.append(gtypes.Content(role="user", parts=parts))
            elif role == "assistant" or role == "model":
                if isinstance(content, str) and content:
                    contents.append(gtypes.Content(
                        role="model",
                        parts=[gtypes.Part(text=content)],
                    ))
                elif isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("function_call"):
                            parts.append(gtypes.Part(
                                function_call=item["function_call"]
                            ))
                        elif isinstance(item, dict) and "text" in item:
                            parts.append(gtypes.Part(text=item["text"]))
                    if parts:
                        contents.append(gtypes.Content(role="model", parts=parts))

        tools_param = [gtypes.Tool(function_declarations=self._tool_declarations)]

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=gtypes.GenerateContentConfig(
                system_instruction=prompts.SYSTEM_PROMPT,
                tools=tools_param,
                max_output_tokens=max_tokens,
            ),
        )

        text = ""
        tool_calls = []

        for part in response.candidates[0].content.parts:
            if part.text:
                text += part.text
            elif part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=fc.name,  # Gemini uses name as ID
                    name=fc.name,
                    input=dict(fc.args) if fc.args else {},
                ))

        wants_tool = len(tool_calls) > 0

        # Token counts
        input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            wants_tool_use=wants_tool,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=response,
        )

    def format_tool_result(self, tool_call: ToolCall, result: str) -> dict:
        from google.genai import types as gtypes
        return {
            "function_response": gtypes.FunctionResponse(
                name=tool_call.name,
                response={"result": result},
            )
        }

    def format_assistant_msg(self, response: LLMResponse) -> dict:
        parts = []
        for part in response.raw.candidates[0].content.parts:
            if part.text:
                parts.append({"text": part.text})
            elif part.function_call:
                fc = part.function_call
                parts.append({
                    "function_call": {
                        "name": fc.name,
                        "args": dict(fc.args) if fc.args else {},
                    }
                })
        return {"role": "model", "content": parts}


# ── factory ──────────────────────────────────────────────────────────

PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai":    OpenAIProvider,
    "google":    GoogleProvider,
}

# Shorthand aliases
MODEL_ALIASES = {
    # Anthropic
    "claude-sonnet":  ("anthropic", "claude-sonnet-4-20250514"),
    "claude-opus":    ("anthropic", "claude-opus-4-20250514"),
    "claude-haiku":   ("anthropic", "claude-haiku-4-5-20251001"),
    "sonnet":         ("anthropic", "sonnet"),
    "opus":           ("anthropic", "opus"),
    "haiku":          ("anthropic", "haiku"),
    # OpenAI
    "gpt-4o":         ("openai", "gpt-4o"),
    "gpt-4o-mini":    ("openai", "gpt-4o-mini"),
    "o4-mini":        ("openai", "o4-mini"),
    "gpt4":           ("openai", "gpt-4-turbo"),
    # Google
    "gemini-flash":   ("google", "gemini-2.0-flash"),
    "gemini-pro":     ("google", "gemini-2.5-pro"),
    "flash":          ("google", "flash"),
    "gemini":         ("google", "gemini-2.0-flash"),
}


def get_provider(provider_name: str, model: str | None = None) -> LLMProvider:
    """
    Create a provider instance.

    provider_name can be:
      - A provider key: "anthropic", "openai", "google"
      - A model alias: "gpt-4o", "gemini-flash", "sonnet"
    """
    # Check if it's a model alias
    if provider_name in MODEL_ALIASES:
        prov, mod = MODEL_ALIASES[provider_name]
        return PROVIDERS[prov](model=mod)

    # Check if the model string is an alias
    if model and model in MODEL_ALIASES:
        prov, mod = MODEL_ALIASES[model]
        return PROVIDERS[prov](model=mod)

    # Direct provider name
    if provider_name not in PROVIDERS:
        available = ", ".join(sorted(PROVIDERS.keys()))
        aliases = ", ".join(sorted(MODEL_ALIASES.keys()))
        raise ValueError(
            f"Unknown provider '{provider_name}'.\n"
            f"  Providers: {available}\n"
            f"  Model aliases: {aliases}"
        )

    cls = PROVIDERS[provider_name]
    if model:
        return cls(model=model)
    return cls()


def list_providers() -> str:
    """Return a formatted string of available providers and models."""
    lines = [
        "Available providers and models:",
        "",
        "  Provider     Model Aliases",
        "  ─────────    ─────────────────────────────────────────",
        "  anthropic    sonnet, opus, haiku, claude-sonnet, claude-opus",
        "               (needs ANTHROPIC_API_KEY)",
        "  openai       gpt-4o, gpt-4o-mini, o4-mini, gpt4",
        "               (needs OPENAI_API_KEY)",
        "  google       gemini, gemini-flash, gemini-pro, flash",
        "               (needs GOOGLE_API_KEY or GEMINI_API_KEY)",
        "",
        "  Usage:  --provider openai --model gpt-4o",
        "     or:  --provider gpt-4o   (shorthand)",
    ]
    return "\n".join(lines)
