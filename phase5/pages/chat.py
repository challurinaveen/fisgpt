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


def _render_message(msg: dict):
    """Render a single chat message with optional metadata."""
    role = msg["role"]
    content = msg["content"]

    with st.chat_message(role, avatar="🍽️" if role == "assistant" else None):
        st.markdown(content)

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
    """Run the answering pipeline and render the result."""
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

    # Generate
    with st.chat_message("assistant", avatar="🍽️"):
        status = st.status("Thinking…", expanded=False)
        t0 = time.time()

        try:
            provider = get_provider(st.session_state.provider_key)
            result = answerer.answer(
                question=question,
                provider=provider,
                verbose=False,
                conversation_history=history or None,
            )
            elapsed = time.time() - t0
            answer_text = result["answer"]
            tokens = result["input_tokens"] + result["output_tokens"]

            # Update status
            tool_summary = ""
            if result["tool_calls"]:
                tool_names = [tc["tool"] for tc in result["tool_calls"]]
                tool_summary = f" · Tools: {', '.join(tool_names)}"
            status.update(
                label=f"Done in {elapsed:.1f}s · {tokens:,} tokens{tool_summary}",
                state="complete",
            )

            # Render answer
            st.markdown(answer_text)

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

            # Save
            st.session_state.messages.append({
                "role": "assistant",
                "content": answer_text,
                "tool_calls": result["tool_calls"],
                "stats": stats,
            })
            st.session_state.total_tokens += tokens
            st.session_state.total_queries += 1

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
