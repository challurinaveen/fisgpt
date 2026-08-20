"""
Phase 4 · F!S internal GPT — interactive chat + golden-question evaluation.

    python fis-gpt/run_phase4.py                           # chat (default: Claude Sonnet)
    python fis-gpt/run_phase4.py --provider openai         # chat with GPT-4o
    python fis-gpt/run_phase4.py --provider gpt-4o-mini    # chat with GPT-4o-mini
    python fis-gpt/run_phase4.py --provider gemini         # chat with Gemini Flash
    python fis-gpt/run_phase4.py --provider anthropic --model opus  # Claude Opus
    python fis-gpt/run_phase4.py --eval                    # run golden questions
    python fis-gpt/run_phase4.py --eval --ids M01 D01      # specific questions
    python fis-gpt/run_phase4.py --providers               # list all providers

Supported providers:
  anthropic  — Claude Sonnet/Opus/Haiku    (needs ANTHROPIC_API_KEY)
  openai     — GPT-4o / GPT-4o-mini       (needs OPENAI_API_KEY)
  google     — Gemini Flash / Pro          (needs GOOGLE_API_KEY)

Prerequisites:
  - Phases 1-3 must have run (warehouse + chunks)
  - pip install anthropic / openai / google-genai  (whichever you use)
  - Set the matching API key environment variable

Output (eval mode): fis-gpt/out/phase4_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# Load .env file (API keys) — looks in fis-gpt/ directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

import config
from phase4 import answerer
from phase4.providers import get_provider, list_providers, LLMProvider


# ── interactive chat ──────────────────────────────────────────────────

def run_chat(provider: LLMProvider, verbose: bool = False) -> None:
    """Interactive CLI chat with the FoodFax GPT."""
    print("\n" + "=" * 62)
    print("  F!S Internal GPT — FoodFax Research Assistant")
    print(f"  Provider: {provider.name} | Model: {provider.model}")
    print("  Type your question and press Enter.")
    print("  Commands:  /quit  /verbose  /model  /help")
    print("=" * 62 + "\n")

    conversation = []

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not question:
            continue
        if question.lower() in ("/quit", "/exit", "/q"):
            print("Goodbye!")
            break
        if question.lower() == "/verbose":
            verbose = not verbose
            print(f"Verbose mode: {'ON' if verbose else 'OFF'}")
            continue
        if question.lower() == "/model":
            print(f"Provider: {provider.name} | Model: {provider.model}")
            continue
        if question.lower() == "/clear":
            conversation = []
            print("Conversation cleared.")
            continue
        if question.lower() == "/help":
            print("""
Commands:
  /quit     Exit the chat
  /verbose  Toggle verbose mode (shows tool calls)
  /model    Show current provider and model
  /clear    Clear conversation history
  /help     Show this help
            """)
            continue

        print()
        t0 = time.time()

        try:
            result = answerer.answer(
                question=question,
                provider=provider,
                verbose=verbose,
                conversation_history=conversation,
            )

            elapsed = time.time() - t0

            print(f"Assistant: {result['answer']}")
            print(f"\n  [{provider.name}/{result['model']} | "
                  f"{result['rounds']} round{'s' if result['rounds'] != 1 else ''}, "
                  f"{len(result['tool_calls'])} tool call{'s' if len(result['tool_calls']) != 1 else ''}, "
                  f"{elapsed:.1f}s, "
                  f"{result['input_tokens'] + result['output_tokens']:,} tokens]\n")

            # Update conversation for multi-turn
            conversation.append({"role": "user", "content": question})
            conversation.append({"role": "assistant", "content": result["answer"]})

        except RuntimeError as e:
            print(f"\nERROR: {e}\n")
        except Exception as e:
            print(f"\nERROR: {e}\n")
            import traceback
            traceback.print_exc()


# ── golden question evaluation ────────────────────────────────────────

def run_eval(
    provider: LLMProvider,
    ids: list[str] | None = None,
    verbose: bool = False,
) -> int:
    """Run golden questions and score the results."""
    import yaml

    golden_path = Path(__file__).parent / "eval" / "golden_questions.yaml"
    with open(golden_path, "r", encoding="utf-8") as f:
        golden = yaml.safe_load(f)

    questions = golden.get("questions", [])
    if ids:
        questions = [q for q in questions if q["id"] in ids]

    if not questions:
        print("No matching questions found.")
        return 1

    print(f"\nF!S Internal GPT — Golden Question Evaluation")
    print(f"Provider: {provider.name} | Model: {provider.model}")
    print(f"Questions: {len(questions)}\n")

    results = []
    total_pass = 0
    total_fail = 0
    total_tokens = 0

    for i, q in enumerate(questions, 1):
        qid = q["id"]
        route = q["route"]
        question = q["question"]

        print(f"[{i}/{len(questions)}] {qid} ({route}): {question[:80]}...")

        t0 = time.time()
        try:
            result = answerer.answer(
                question=question,
                provider=provider,
                verbose=verbose,
            )
            elapsed = time.time() - t0
            answer_text = result["answer"]
            tokens = result["input_tokens"] + result["output_tokens"]
            total_tokens += tokens

            # Score the answer
            score = _score_answer(q, answer_text)
            status = "PASS" if score["pass"] else "FAIL"

            if score["pass"]:
                total_pass += 1
            else:
                total_fail += 1

            print(f"  {status} ({elapsed:.1f}s, {tokens:,} tok) — {score['reason']}")

            results.append({
                "id": qid,
                "route": route,
                "question": question,
                "answer": answer_text,
                "tool_calls": result["tool_calls"],
                "score": score,
                "elapsed_s": elapsed,
                "tokens": tokens,
                "rounds": result["rounds"],
            })

        except Exception as e:
            elapsed = time.time() - t0
            total_fail += 1
            print(f"  ERROR ({elapsed:.1f}s) — {e}")
            results.append({
                "id": qid,
                "route": route,
                "question": question,
                "error": str(e),
                "elapsed_s": elapsed,
            })

    # Summary
    total = total_pass + total_fail
    print(f"\n{'=' * 62}")
    print(f"  Provider: {provider.name} | Model: {provider.model}")
    print(f"  Results: {total_pass}/{total} passed ({100 * total_pass / total:.0f}%)")
    print(f"  Total tokens: {total_tokens:,}")
    print(f"{'=' * 62}\n")

    # Save results
    eval_output = {
        "provider": provider.name,
        "model": provider.model,
        "n_questions": total,
        "passed": total_pass,
        "failed": total_fail,
        "total_tokens": total_tokens,
        "results": results,
    }
    output_path = config.OUT_DIR / "phase4_eval.json"
    output_path.write_text(
        json.dumps(eval_output, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"  Saved to {output_path}")

    return 0 if total_fail == 0 else 1


def _score_answer(question_spec: dict, answer_text: str) -> dict:
    """Score an answer against the golden question specification."""
    answer_lower = answer_text.lower()
    route = question_spec["route"]
    reasons = []
    passed = True

    # 1. Check expected substrings
    expects = question_spec.get("expects") or []
    for expected in expects:
        if expected.lower() not in answer_lower:
            reasons.append(f"missing '{expected}'")
            passed = False

    # 2. Check expected numeric values
    expects_value = question_spec.get("expects_value")
    if expects_value is not None:
        if isinstance(expects_value, list):
            for v in expects_value:
                if str(v) not in answer_text:
                    reasons.append(f"missing value {v}")
                    passed = False
        else:
            # Allow comma-formatted numbers
            v_str = str(expects_value)
            v_comma = f"{expects_value:,}" if isinstance(expects_value, int) else v_str
            if v_str not in answer_text and v_comma not in answer_text:
                reasons.append(f"missing value {expects_value}")
                passed = False

    # 3. Check citation requirements
    must_cite = question_spec.get("must_cite") or []
    for cite in must_cite:
        if cite.lower() not in answer_lower:
            reasons.append(f"missing citation '{cite}'")
            passed = False

    # 4. For refuse route, check that the answer doesn't fabricate
    if route == "refuse":
        refusal_signals = [
            "not available", "don't have", "do not have", "cannot",
            "no data", "not in", "absent", "missing", "unable",
            "outside", "not included", "failed to download", "scrubbed",
            "not possible", "can't", "unavailable", "doesn't",
            "i don't", "excluded", "not accessible",
        ]
        has_refusal = any(signal in answer_lower for signal in refusal_signals)
        if not has_refusal:
            reasons.append("should have refused but didn't")
            passed = False
        else:
            passed = True  # Refuse questions pass if they refuse

    if not reasons:
        reasons.append("all checks passed")

    return {"pass": passed, "reason": "; ".join(reasons)}


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="F!S Internal GPT — FoodFax Research Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python run_phase4.py                           # Claude Sonnet (default)
  python run_phase4.py --provider openai         # GPT-4o
  python run_phase4.py --provider gpt-4o-mini    # GPT-4o-mini (shorthand)
  python run_phase4.py --provider gemini         # Gemini Flash
  python run_phase4.py --provider anthropic --model opus    # Claude Opus
  python run_phase4.py --eval --ids M01 D01      # eval specific questions
  python run_phase4.py --providers               # list all options
        """,
    )
    parser.add_argument(
        "--provider", "-p", default="anthropic",
        help="LLM provider or model alias (anthropic, openai, google, gpt-4o, gemini, etc.)"
    )
    parser.add_argument(
        "--model", "-m", default=None,
        help="Model name within the provider (e.g. opus, gpt-4o-mini, gemini-2.5-pro)"
    )
    parser.add_argument(
        "--eval", action="store_true",
        help="Run golden question evaluation instead of interactive chat"
    )
    parser.add_argument(
        "--ids", nargs="*",
        help="Specific question IDs to evaluate (e.g. M01 D01 H01)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show tool calls and intermediate results"
    )
    parser.add_argument(
        "--providers", action="store_true",
        help="List all available providers and model aliases"
    )

    args = parser.parse_args()

    if args.providers:
        print(list_providers())
        return 0

    # Build provider
    try:
        provider = get_provider(args.provider, args.model)
    except ImportError as e:
        pkg_hints = {
            "anthropic": "pip install anthropic",
            "openai":    "pip install openai",
            "google":    "pip install google-genai",
        }
        hint = pkg_hints.get(args.provider, "pip install <provider-sdk>")
        print(f"ERROR: {e}\n  Install it with:  {hint}")
        return 1
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1

    if args.eval:
        return run_eval(provider, args.ids, args.verbose)
    else:
        run_chat(provider, args.verbose)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
