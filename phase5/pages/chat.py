"""
Chat page — the main conversational interface.
"""
from __future__ import annotations

import sys
from pathlib import Path
_FIS_GPT = str(Path(__file__).resolve().parent.parent.parent)
if _FIS_GPT not in sys.path:
    sys.path.insert(0, _FIS_GPT)

import json
import time

import streamlit as st

from phase4 import answerer
from phase4.providers import get_provider
from phase4.tools import get_pending_charts
from phase5.audit_log import log_query


# ── sample questions ──────────────────────────────────────────────────

SAMPLE_QUESTIONS = [
    ("📊", "How many product tests do we have in total?"),
    ("🏆", "Which categories have we tested most often?"),
    ("💬", "What did reviewers say about Jacob's Red Leicester Flavour Bites?"),
    ("👎", "Summarise the negative feedback on Bio & Me Good Gut Puffs"),
    ("⭐", "Which products in 2025 beat their category average?"),
    ("📝", "Draft a client summary for Set 26"),
    ("🌱", "We're pitching a vegan range — what have we learned?"),
    ("📈", "Has Value for Money improved or declined since 2016?"),
]


# ── rendering ─────────────────────────────────────────────────────────

def _render_welcome():
    """Welcome screen with sample questions."""
    st.markdown("""
    ### Welcome 👋

    Ask me anything about **30+ years of FoodFax product testing data** —
    scores, trends, verbatims, category norms, and more.

    I can run SQL against the database, search document chunks, and
    combine both for comprehensive answers.

    ---
    **Try one of these:**
    """)

    cols = st.columns(2)
    for i, (icon, question) in enumerate(SAMPLE_QUESTIONS):
        with cols[i % 2]:
            if st.button(
                f"{icon}  {question}",
                key=f"sample_{i}",
                use_container_width=True,
            ):
                st.session_state.pending_question = question
                st.rerun()


def _render_charts(charts: list[dict]):
    """Render charts stored by the create_chart tool."""
    for chart in charts:
        chart_type = chart.get("type", "bar")
        title = chart.get("title", "Chart")
        data = chart.get("data")

        if data is None or len(data) == 0:
            continue

        st.markdown(f"**{title}**")

        # Use the first column as the index
        index_col = data.columns[0]
        chart_df = data.set_index(index_col)

        if chart_type == "line":
            st.line_chart(chart_df)
        elif chart_type == "area":
            st.area_chart(chart_df)
        else:
            st.bar_chart(chart_df)


def _render_message(msg: dict):
    """Render a single chat message with optional metadata."""
    role = msg["role"]
    content = msg["content"]

    with st.chat_message(role, avatar="🍽️" if role == "assistant" else None):
        st.markdown(content)

        # Charts (if any were generated with this message)
        if role == "assistant" and msg.get("charts"):
            _render_charts(msg["charts"])

        # Tool calls (if verbose)
        if (role == "assistant"
                and st.session_state.get("verbose")
                and msg.get("tool_calls")):
            with st.expander(
                f"🔧 {len(msg['tool_calls'])} tool call(s)",
                expanded=False,
            ):
                for tc in msg["tool_calls"]:
                    st.code(
                        f"{tc['tool']}({json.dumps(tc['input'], indent=2)})",
                        language="json",
                    )

        # Stats footer
        if role == "assistant" and msg.get("stats"):
            s = msg["stats"]
            parts = []
            if s.get("elapsed"):
                parts.append(f"⏱️ {s['elapsed']:.1f}s")
            if s.get("tokens"):
                parts.append(f"📊 {s['tokens']:,} tok")
            if s.get("rounds"):
                parts.append(f"🔄 {s['rounds']} round{'s' if s['rounds'] != 1 else ''}")
            if s.get("model"):
                parts.append(f"🤖 {s['model']}")
            if parts:
                st.caption(" · ".join(parts))


def _generate_answer(question: str):
    """Run the answering pipeline with streaming output."""
    # Save user message
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Build multi-turn history
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
        if m["role"] in ("user", "assistant")
    ]

    # Generate — with streaming
    with st.chat_message("assistant", avatar="🍽️"):
        status = st.status("Thinking…", expanded=False)
        text_placeholder = st.empty()
        t0 = time.time()

        try:
            provider = get_provider(st.session_state.provider_key)
            answer_text = ""
            result = None

            # Use streaming if available, fall back to blocking
            _stream_fn = getattr(answerer, "answer_stream", None)

            if _stream_fn is not None:
                for event in _stream_fn(
                    question=question,
                    provider=provider,
                    conversation_history=history or None,
                ):
                    if event["type"] == "status":
                        status.update(label=event["msg"])
                    elif event["type"] == "token":
                        answer_text += event["text"]
                        text_placeholder.markdown(answer_text + " ▌")
                    elif event["type"] == "done":
                        result = event["result"]
            else:
                # Fallback — non-streaming
                result = answerer.answer(
                    question=question,
                    provider=provider,
                    verbose=False,
                    conversation_history=history or None,
                )

            elapsed = time.time() - t0

            if result is None:
                raise RuntimeError("No response from LLM")

            answer_text = result["answer"]
            tokens = result["input_tokens"] + result["output_tokens"]

            # Final render (removes cursor)
            text_placeholder.markdown(answer_text)

            # Update status
            tool_summary = ""
            if result["tool_calls"]:
                tool_names = [tc["tool"] for tc in result["tool_calls"]]
                tool_summary = f" · Tools: {', '.join(tool_names)}"
            status.update(
                label=f"Done in {elapsed:.1f}s · {tokens:,} tokens{tool_summary}",
                state="complete",
            )

            # Render any charts created by the create_chart tool
            charts = get_pending_charts()
            if charts:
                _render_charts(charts)

            # Stats
            stats = {
                "elapsed": elapsed,
                "tokens": tokens,
                "rounds": result["rounds"],
                "model": result["model"],
            }
            parts = [
                f"⏱️ {elapsed:.1f}s",
                f"📊 {tokens:,} tok",
                f"🔄 {result['rounds']} round{'s' if result['rounds'] != 1 else ''}",
                f"🤖 {result['model']}",
            ]
            st.caption(" · ".join(parts))

            # Tool calls (verbose)
            if st.session_state.get("verbose") and result["tool_calls"]:
                with st.expander(
                    f"🔧 {len(result['tool_calls'])} tool call(s)",
                    expanded=False,
                ):
                    for tc in result["tool_calls"]:
                        st.code(
                            f"{tc['tool']}({json.dumps(tc['input'], indent=2)})",
                            language="json",
                        )

            # Save to session (include charts for replay)
            st.session_state.messages.append({
                "role": "assistant",
                "content": answer_text,
                "tool_calls": result["tool_calls"],
                "charts": charts if charts else None,
                "stats": stats,
            })
            st.session_state.total_tokens += tokens
            st.session_state.total_queries += 1

            # Persist to audit log
            log_query(
                question=question,
                answer=answer_text,
                tool_calls=result["tool_calls"],
                model=result["model"],
                provider=result["provider"],
                tokens=tokens,
                rounds=result["rounds"],
                elapsed_s=elapsed,
            )

        except Exception as e:
            elapsed = time.time() - t0
            status.update(label=f"Error after {elapsed:.1f}s", state="error")
            error_msg = f"❌ **Error:** {e}"
            st.error(str(e))
            st.session_state.messages.append({
                "role": "assistant",
                "content": error_msg,
            })


# ── page entry point ──────────────────────────────────────────────────

def render():
    """Main render function called by st.navigation."""
    if not st.session_state.messages:
        _render_welcome()
    else:
        for msg in st.session_state.messages:
            _render_message(msg)

    # Handle pending question from sample buttons
    if "pending_question" in st.session_state:
        q = st.session_state.pop("pending_question")
        _generate_answer(q)

    # Chat input
    if question := st.chat_input("Ask about FoodFax data…"):
        _generate_answer(question)
