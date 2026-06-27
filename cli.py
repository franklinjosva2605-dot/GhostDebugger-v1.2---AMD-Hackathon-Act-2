"""
cli.py

Command-line entry point for GhostDebugger — useful for quick testing
without spinning up Streamlit.

Usage:
    python cli.py path/to/buggy_script.py
    python cli.py path/to/buggy_script.py --error "paste traceback here"
    echo 'print(1/0)' | python cli.py -
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.llm_client import LLMClient
from core.orchestrator import GhostDebuggerPipeline


def main():
    parser = argparse.ArgumentParser(description="GhostDebugger CLI — multi-agent debugging pipeline")
    parser.add_argument("file", help="Path to the buggy Python file, or '-' to read from stdin")
    parser.add_argument("--error", default="", help="Optional error message/traceback you observed")
    parser.add_argument("--timeout", type=int, default=8, help="Sandbox execution timeout in seconds")
    args = parser.parse_args()

    if args.file == "-":
        code = sys.stdin.read()
    else:
        path = Path(args.file)
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(1)
        code = path.read_text(encoding="utf-8")

    pipeline = GhostDebuggerPipeline(LLMClient())

    def progress(stage: str):
        print(f"  -> {stage}")

    print("Running GhostDebugger pipeline...\n")
    result = pipeline.run(code, args.error, timeout=args.timeout, progress_callback=progress)

    print("\n" + "=" * 60)
    print(f"CLASSIFICATION : {result.routing.complexity.upper()} (confidence {result.routing.confidence:.0%})")
    print(f"REASONING      : {result.routing.reasoning}")
    print("-" * 60)
    print(f"REPRODUCTION   : {result.reproduction.summary}")
    if result.trace:
        print("-" * 60)
        print(f"ROOT CAUSE     : {result.trace.root_cause}")
        print(f"EXECUTION PATH : {result.trace.execution_path}")
    if result.fix:
        print("-" * 60)
        status = "VERIFIED ✅" if result.fix.success else "NOT VERIFIED ❌"
        print(f"FIX STATUS     : {status} ({result.fix.attempts} attempt(s))")
        if result.fix.fixed_code:
            print("\nFIXED CODE:")
            print(result.fix.fixed_code)
    if result.review:
        print("-" * 60)
        print("REVIEW:")
        print(result.review.explanation)
    print("=" * 60)
    print(
        f"Tokens used: {result.total_tokens_used} | "
        f"Baseline (always-70B): {result.baseline_tokens_always_70b} | "
        f"Saved: {result.savings_percent}% | "
        f"Latency: {result.total_latency_seconds}s"
    )


if __name__ == "__main__":
    main()

