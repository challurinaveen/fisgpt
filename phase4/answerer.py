"""
Phase 4 · Answerer — the core LLM tool-use loop.

Takes a user question, sends it to the LLM with the FoodFax system prompt
and tools, handles the tool-use loop, and returns the final answer.

Supports multiple providers (Anthropic, OpenAI, Google) through the
provider abstraction in providers.py.
"""
from __future__ import annotations

import json
from typing import Generator

from phase4 import prompts, tools
from phase4.providers import LLMProvider, get_provider


# ── configuration ──────────────────────────────────────────────────────

MAX_TOOL_ROUNDS = 8          # safety cap on tool-use loops
MAX_TOKENS = 4096


# ── main answering function ──────────────────────────────────────────

def answer(
    question: str,
    provider: LLMProvider | None = None,
    provider_name: str = "anthropic",
    model: str | None = None,
    verbose: bool = False,
    conversation_history: list | None = None,
) -> dict:
    """
    Answer a question about the FoodFax database.

    Args:
        question:   The user's question
        provider:   A pre-built LLMProvider instance (takes priority)
        provider_name: Provider key or model alias (e.g. "openai", "gpt-4o")
        model:      Model name within the provider
        verbose:    Print tool calls and intermediate results
        conversation_history: Previous messages for multi-turn

    Returns:
        {
            "answer": str,
            "tool_calls": [...],
            "provider": str,
            "model": str,
            "input_tokens": int,
            "output_tokens": int,
            "rounds": int,
        }
    """
    if provider is None:
        provider = get_provider(provider_name, model)

    # Build messages
    if conversation_history:
        messages = list(conversation_history)
        messages.append({"role": "user", "content": question})
    else:
        messages = [{"role": "user", "content": question}]

    tool_calls_log = []
    total_input_tokens = 0
    total_output_tokens = 0

    # Tool-use loop
    for round_num in range(MAX_TOOL_ROUNDS):
        if verbose:
            print(f"    [round {round_num + 1}] sending to {provider.name}...")

        response = provider.chat(messages, max_tokens=MAX_TOKENS)

        total_input_tokens += response.input_tokens
        total_output_tokens += response.output_tokens

        if response.wants_tool_use and response.tool_calls:
            # Process tool calls
            tool_result_msgs = []

            for tc in response.tool_calls:
                if verbose:
                    print(f"    [tool] {tc.name}({json.dumps(tc.input)[:200]})")

                result = tools.dispatch_tool(tc.name, tc.input)

                if verbose:
                    preview = result[:200] + "..." if len(result) > 200 else result
                    print(f"    [result] {preview}")

                tool_calls_log.append({
                    "tool": tc.name,
                    "input": tc.input,
                    "result_length": len(result),
                })

                tool_result_msgs.append(provider.format_tool_result(tc, result))

            # Add assistant response + tool results to messages
            messages.append(provider.format_assistant_msg(response))

            # OpenAI: tool results are separate messages
            if provider.name == "openai":
                for tr in tool_result_msgs:
                    messages.append(tr)
            # Anthropic: tool results go in a single user message
            elif provider.name == "anthropic":
                messages.append({"role": "user", "content": tool_result_msgs})
            # Google: tool results go in a single user content list
            elif provider.name == "google":
                messages.append({"role": "user", "content": tool_result_msgs})

        else:
            # Model is done — return the answer
            return {
                "answer": response.text,
                "tool_calls": tool_calls_log,
                "provider": provider.name,
                "model": getattr(provider, "model", "unknown"),
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "rounds": round_num + 1,
            }

    # Hit the safety cap
    return {
        "answer": "(Maximum tool-use rounds reached. The question may need to be broken down.)",
        "tool_calls": tool_calls_log,
        "provider": provider.name,
        "model": getattr(provider, "model", "unknown"),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "rounds": MAX_TOOL_ROUNDS,
    }


# ── streaming version ───────────────────────────────────────────────

_TOOL_STATUS = {
    "run_sql":      "📊 Running SQL query…",
    "search_docs":  "🔍 Searching documents…",
    "create_chart": "📈 Creating chart…",
}


def answer_stream(
    question: str,
    provider: LLMProvider | None = None,
    provider_name: str = "openai",
    model: str | None = None,
    conversation_history: list | None = None,
) -> Generator[dict, None, None]:
    """
    Streaming version of answer().

    Yields event dicts:
        {"type": "status",  "msg": "📊 Running SQL query…"}
        {"type": "token",   "text": "The"}
        {"type": "done",    "result": { ... same as answer() ... }}
    """
    if provider is None:
        provider = get_provider(provider_name, model)

    # Build messages
    if conversation_history:
        messages = list(conversation_history)
        messages.append({"role": "user", "content": question})
    else:
        messages = [{"role": "user", "content": question}]

    tool_calls_log: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0

    has_streaming = hasattr(provider, "chat_stream")

    for round_num in range(MAX_TOOL_ROUNDS):
        if has_streaming:
            # ── streaming path ──
            stream = provider.chat_stream(messages, max_tokens=MAX_TOKENS)
            response = None

            for event_type, event_data in stream:
                if event_type == "token":
                    yield {"type": "token", "text": event_data}
                elif event_type in ("tool_calls", "done"):
                    response = event_data

            if response is None:
                break

            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens

            if response.wants_tool_use and response.tool_calls:
                tool_result_msgs = []

                for tc in response.tool_calls:
                    yield {"type": "status", "msg": _TOOL_STATUS.get(tc.name, f"🔧 Using {tc.name}…")}

                    result = tools.dispatch_tool(tc.name, tc.input)
                    tool_calls_log.append({
                        "tool": tc.name,
                        "input": tc.input,
                        "result_length": len(result),
                    })
                    tool_result_msgs.append(provider.format_tool_result(tc, result))

                messages.append(provider.format_assistant_msg(response))
                # OpenAI: tool results as separate messages
                if provider.name == "openai":
                    for tr in tool_result_msgs:
                        messages.append(tr)
                else:
                    messages.append({"role": "user", "content": tool_result_msgs})
            else:
                # Final answer
                yield {
                    "type": "done",
                    "result": {
                        "answer": response.text,
                        "tool_calls": tool_calls_log,
                        "provider": provider.name,
                        "model": getattr(provider, "model", "unknown"),
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "rounds": round_num + 1,
                    },
                }
                return
        else:
            # ── non-streaming fallback ──
            yield {"type": "status", "msg": "🤔 Thinking…"}
            response = provider.chat(messages, max_tokens=MAX_TOKENS)
            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens

            if response.wants_tool_use and response.tool_calls:
                tool_result_msgs = []
                for tc in response.tool_calls:
                    yield {"type": "status", "msg": _TOOL_STATUS.get(tc.name, f"🔧 Using {tc.name}…")}
                    result = tools.dispatch_tool(tc.name, tc.input)
                    tool_calls_log.append({
                        "tool": tc.name,
                        "input": tc.input,
                        "result_length": len(result),
                    })
                    tool_result_msgs.append(provider.format_tool_result(tc, result))

                messages.append(provider.format_assistant_msg(response))
                if provider.name == "openai":
                    for tr in tool_result_msgs:
                        messages.append(tr)
                elif provider.name == "anthropic":
                    messages.append({"role": "user", "content": tool_result_msgs})
                elif provider.name == "google":
                    messages.append({"role": "user", "content": tool_result_msgs})
            else:
                yield {
                    "type": "done",
                    "result": {
                        "answer": response.text,
                        "tool_calls": tool_calls_log,
                        "provider": provider.name,
                        "model": getattr(provider, "model", "unknown"),
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "rounds": round_num + 1,
                    },
                }
                return

    # Safety cap
    yield {
        "type": "done",
        "result": {
            "answer": "(Maximum tool-use rounds reached. The question may need to be broken down.)",
            "tool_calls": tool_calls_log,
            "provider": provider.name,
            "model": getattr(provider, "model", "unknown"),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "rounds": MAX_TOOL_ROUNDS,
        },
    }
