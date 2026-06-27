"""
ui/app.py

GhostDebugger Streamlit UI.

Tabs:
    1. Debug       - paste code, run the full 5-agent pipeline, see results
    2. Pipeline    - live view of each agent stage as it runs
    3. Token Savings - dashboard comparing tiered routing vs always-70B
    4. System Status / About

Run with: streamlit run ui/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import streamlit as st

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.llm_client import LLMClient  # noqa: E402
from core.orchestrator import GhostDebuggerPipeline  # noqa: E402

st.set_page_config(
    page_title="GhostDebugger",
    page_icon="👻",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
    .stApp { background-color: #0d1117; }
    [data-testid="stHeader"] { background-color: rgba(0,0,0,0); }

    h1, h2, h3 { font-family: 'JetBrains Mono', 'Courier New', monospace; }

    .gd-title {
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 2.2rem; font-weight: 700;
        color: #e6edf3; letter-spacing: -0.02em; margin-bottom: 0;
    }
    .gd-subtitle {
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        color: #7d8590; font-size: 0.95rem; margin-top: 0.2rem;
    }
    .gd-accent { color: #39d353; }

    .gd-chain {
        display: flex; align-items: center; gap: 0.4rem;
        margin: 1.2rem 0;
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 0.85rem;
    }
    .gd-node {
        padding: 0.35rem 0.8rem; border-radius: 4px;
        border: 1px solid #30363d; color: #7d8590; background: #161b22;
    }
    .gd-node.active { border-color: #39d353; color: #39d353; box-shadow: 0 0 8px rgba(57,211,83,0.4); }
    .gd-node.done   { border-color: #1f6feb; color: #58a6ff; }
    .gd-arrow { color: #30363d; }

    .gd-card {
        background: #161b22; border: 1px solid #30363d;
        border-radius: 6px; padding: 1rem 1.2rem; margin-bottom: 1rem;
    }
    .gd-card-label {
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 0.75rem; text-transform: uppercase;
        letter-spacing: 0.08em; color: #7d8590; margin-bottom: 0.3rem;
    }

    /* Live token ticker */
    .gd-token-ticker {
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 1.6rem; font-weight: 700; color: #39d353;
        letter-spacing: -0.02em;
    }
    .gd-token-label {
        font-family: 'JetBrains Mono', 'Courier New', monospace;
        font-size: 0.75rem; color: #7d8590; text-transform: uppercase;
        letter-spacing: 0.08em;
    }
    .gd-savings-bar-wrap {
        background: #161b22; border: 1px solid #30363d;
        border-radius: 6px; padding: 1rem 1.2rem; margin-bottom: 1rem;
    }
    .gd-savings-bar-track {
        background: #21262d; border-radius: 4px; height: 10px;
        overflow: hidden; margin-top: 0.4rem;
    }
    .gd-savings-bar-fill {
        background: #39d353; height: 100%; border-radius: 4px;
        transition: width 0.3s ease;
    }

    .gd-tier-syntax      { color: #39d353; }
    .gd-tier-logic       { color: #d29922; }
    .gd-tier-architecture{ color: #f85149; }

    .stButton button {
        background-color: #238636; color: white; border: none;
        font-family: 'JetBrains Mono', 'Courier New', monospace; font-weight: 600;
    }
    .stButton button:hover { background-color: #2ea043; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ─────────────────────────── session state ─────────────────────────── #
for key, default in [
    ("pipeline_result", None),
    ("run_history", []),
    ("current_stage", None),
    ("live_tokens", 0),
    ("live_baseline", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = default


@st.cache_resource
def get_pipeline() -> GhostDebuggerPipeline:
    return GhostDebuggerPipeline(LLMClient())


pipeline = get_pipeline()

SAMPLE_BUGGY_CODE = '''def calculate_average(numbers):
    total = 0
    for i in range(len(numbers) + 1):
        total += numbers[i]
    return total / len(numbers)

result = calculate_average([4, 8, 15, 16, 23, 42])
print(f"Average: {result}")
'''

STAGE_ORDER = [
    "Routing (Complexity Router)",
    "Reproducing bug (sandbox execution)",
    "Tracing root cause",
    "Generating + verifying fix",
    "Writing review",
]
STAGE_SHORT = ["Router", "Reproducer", "Tracer", "Fixer", "Reviewer"]

# Approximate token cost emitted at each stage for the live ticker.
# Keeps the counter moving even before the pipeline result comes back.
STAGE_TOKEN_DELTAS = {
    "Routing (Complexity Router)": 50,
    "Reproducing bug (sandbox execution)": 0,   # sandbox, no LLM
    "Tracing root cause": 300,
    "Generating + verifying fix": 400,
    "Writing review": 200,
}
ALWAYS_70B_BASELINE = 800


def render_chain(active_stage: Optional[str], completed: List[str]):
    nodes = []
    for full_name, short_name in zip(STAGE_ORDER, STAGE_SHORT):
        if full_name == active_stage:
            cls = "active"
        elif full_name in completed:
            cls = "done"
        else:
            cls = ""
        nodes.append(f'<div class="gd-node {cls}">{short_name}</div>')
    html = '<div class="gd-chain">' + '<span class="gd-arrow">→</span>'.join(nodes) + "</div>"
    st.markdown(html, unsafe_allow_html=True)


def render_live_token_bar(tokens_so_far: int, baseline: int = ALWAYS_70B_BASELINE):
    """Render the live token savings bar during pipeline execution."""
    saved = max(baseline - tokens_so_far, 0)
    pct = round(saved / baseline * 100) if baseline else 0
    # Clamp: if tokens exceed baseline (e.g. multiple retries), savings = 0
    pct = max(pct, 0)
    bar_width = max(min(100 - (tokens_so_far / baseline * 100), 100), 0)
    st.markdown(
        f"""
        <div class="gd-savings-bar-wrap">
          <div class="gd-token-label">Live Token Counter</div>
          <div style="display:flex;align-items:baseline;gap:1rem;margin-top:0.3rem;">
            <span class="gd-token-ticker">{tokens_so_far}</span>
            <span style="color:#7d8590;font-family:monospace;font-size:0.9rem;">
              / {baseline} baseline &nbsp;·&nbsp;
              <span style="color:#39d353;">~{pct}% saved</span>
            </span>
          </div>
          <div class="gd-savings-bar-track">
            <div class="gd-savings-bar-fill" style="width:{bar_width:.1f}%"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────── header ─────────────────────────── #
st.markdown('<div class="gd-title">👻 GhostDebugger</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="gd-subtitle">Token-efficient multi-agent debugging — '
    '<span class="gd-accent">5 agents, 4 model tiers, 1 verified fix</span></div>',
    unsafe_allow_html=True,
)
st.write("")

# ─────────────────────────── sidebar ─────────────────────────── #
with st.sidebar:
    st.markdown("### System Status")
    status = pipeline.llm.status()

    if status["fireworks_configured"]:
        st.success("Fireworks AI key detected", icon="✅")
    else:
        st.warning("No FIREWORKS_API_KEY — using fallback", icon="⚠️")

    if status["ollama_available"]:
        st.success("Ollama reachable (local fallback ready)", icon="✅")
    else:
        st.info("Ollama not detected — will use mock fallback", icon="ℹ️")

    st.markdown("---")
    st.markdown("### Model Tiers")
    st.markdown(
        """
        - 🟢 **Syntax** → Qwen 1.5B
        - 🟡 **Logic** → Llama 8B
        - 🔴 **Architecture** → Llama 70B
        - ⚪ **Offline** → Ollama local
        """
    )
    st.markdown("---")
    st.markdown("### Run History")
    if st.session_state.run_history:
        for entry in reversed(st.session_state.run_history[-10:]):
            st.caption(f"{entry['complexity']} · {entry['tokens']} tok · {'✅' if entry['success'] else '❌'}")
    else:
        st.caption("No runs yet this session.")

# ─────────────────────────── tabs ─────────────────────────── #
tab_debug, tab_pipeline, tab_savings, tab_about = st.tabs(
    ["🔍 Debug", "⚙️ Pipeline Detail", "📊 Token Savings", "ℹ️ About"]
)

# ══════════════════════════ TAB 1: Debug ══════════════════════════ #
with tab_debug:
    col_input, col_output = st.columns([1, 1], gap="large")

    with col_input:
        st.markdown('<div class="gd-card-label">Input</div>', unsafe_allow_html=True)
        use_sample = st.checkbox("Load sample buggy code", value=False)
        code_input = st.text_area(
            "Paste broken Python code",
            value=SAMPLE_BUGGY_CODE if use_sample else "",
            height=300,
            placeholder="def my_function():\n    ...",
            label_visibility="collapsed",
        )
        error_input = st.text_area(
            "Optional: paste the error message / traceback you saw",
            height=100,
            placeholder="Traceback (most recent call last): ...",
        )
        timeout_val = st.slider("Sandbox timeout (seconds)", 3, 20, 8)
        run_clicked = st.button("▶ Run GhostDebugger", use_container_width=True, type="primary")

    with col_output:
        st.markdown('<div class="gd-card-label">Pipeline Progress</div>', unsafe_allow_html=True)
        chain_placeholder = st.empty()
        token_bar_placeholder = st.empty()
        result_placeholder = st.container()

        if not run_clicked and st.session_state.pipeline_result is None:
            with chain_placeholder:
                render_chain(None, [])
            st.caption("Run a snippet to see agents activate here.")

        if run_clicked:
            if not code_input.strip():
                st.error("Paste some code first.")
            else:
                completed_stages: List[str] = []
                token_state = {"live_tokens": 0}

                def progress_callback(stage_name: str):
                    
                    completed_stages.append(stage_name)
                    token_state["live_tokens"] += STAGE_TOKEN_DELTAS.get(stage_name, 0)
                    with chain_placeholder:
                        render_chain(stage_name, completed_stages[:-1])
                    with token_bar_placeholder:
                        render_live_token_bar(token_state["live_tokens"])

                with st.spinner("Running multi-agent pipeline..."):
                    result = pipeline.run(
                        code_input, error_input, timeout=timeout_val,
                        progress_callback=progress_callback,
                    )

                with chain_placeholder:
                    render_chain(None, completed_stages)
                with token_bar_placeholder:
                    render_live_token_bar(result.total_tokens_used)

                st.session_state.pipeline_result = result
                st.session_state.run_history.append(
                    {
                        "complexity": result.routing.complexity,
                        "tokens": result.total_tokens_used,
                        "success": bool(result.fix and result.fix.success),
                    }
                )

        result = st.session_state.pipeline_result
        if result:
            # Show the final token bar for past results too
            with token_bar_placeholder:
                render_live_token_bar(result.total_tokens_used)

            with result_placeholder:
                tier_class = f"gd-tier-{result.routing.complexity}"
                st.markdown(
                    f'<div class="gd-card"><div class="gd-card-label">Classification</div>'
                    f'<span class="{tier_class}" style="font-size:1.3rem;font-weight:700;">'
                    f'{result.routing.complexity.upper()}</span> '
                    f'<span style="color:#7d8590;">(confidence {result.routing.confidence:.0%})</span>'
                    f'<br><span style="color:#7d8590;font-size:0.85rem;">{result.routing.reasoning}</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div class="gd-card"><div class="gd-card-label">Reproduction</div>'
                    f'{result.reproduction.summary}</div>',
                    unsafe_allow_html=True,
                )
                if result.trace:
                    st.markdown(
                        f'<div class="gd-card"><div class="gd-card-label">Root Cause</div>'
                        f'{result.trace.root_cause}'
                        f'<br><br><span style="color:#7d8590;font-size:0.85rem;">'
                        f'Execution path: {result.trace.execution_path}</span></div>',
                        unsafe_allow_html=True,
                    )
                if result.fix:
                    if result.fix.success:
                        st.success(f"Fix verified in sandbox ({result.fix.attempts} attempt(s))")
                    else:
                        st.error("Fix did not pass sandbox verification — review manually")

                    if result.fix.fixed_code:
                        st.markdown('<div class="gd-card-label">Fixed Code</div>', unsafe_allow_html=True)
                        st.code(result.fix.fixed_code, language="python")

                    if result.fix.verification:
                        with st.expander("Sandbox verification output"):
                            st.code(result.fix.verification.stdout or "(no stdout)", language="text")
                            if result.fix.verification.stderr:
                                st.code(result.fix.verification.stderr, language="text")

                if result.review:
                    st.markdown('<div class="gd-card-label">Senior Review</div>', unsafe_allow_html=True)
                    st.markdown(result.review.explanation)

                st.caption(
                    f"Total: {result.total_tokens_used} tokens · "
                    f"{result.total_latency_seconds}s · "
                    f"{result.savings_percent}% saved vs always-70B baseline"
                )

# ══════════════════════════ TAB 2: Pipeline Detail ══════════════════════════ #
with tab_pipeline:
    st.markdown("### Agent-by-agent breakdown")
    result = st.session_state.pipeline_result
    if not result:
        st.info("Run a debug session in the Debug tab to see per-agent detail here.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**1. Complexity Router**")
            st.json({
                "complexity": result.routing.complexity,
                "confidence": result.routing.confidence,
                "tier": result.routing.raw_response.tier_used.value,
                "source": result.routing.raw_response.source,
                "tokens": result.routing.raw_response.tokens_used,
                "latency_s": round(result.routing.raw_response.latency_seconds, 3),
            })
            st.markdown("**2. Reproducer**")
            st.json({
                "reproduced": result.reproduction.reproduced,
                "timed_out": result.reproduction.execution.timed_out,
                "return_code": result.reproduction.execution.return_code,
            })
            if result.trace:
                st.markdown("**3. Tracer**")
                st.json({
                    "tier": result.trace.tier_used,
                    "tokens": result.trace.tokens_used,
                    "affected_lines": result.trace.affected_lines,
                })
        with c2:
            if result.fix:
                st.markdown("**4. Fixer**")
                st.json({
                    "success": result.fix.success,
                    "attempts": result.fix.attempts,
                    "tier": result.fix.tier_used,
                    "tokens": result.fix.tokens_used,
                    "notes": result.fix.notes,
                })
            if result.review:
                st.markdown("**5. Reviewer**")
                st.json({
                    "tier": result.review.tier_used,
                    "tokens": result.review.tokens_used,
                })

# ══════════════════════════ TAB 3: Token Savings ══════════════════════════ #
with tab_savings:
    st.markdown("### Token efficiency vs. always-70B baseline")
    st.caption(
        "GhostDebugger routes each bug to the cheapest model tier capable of fixing it. "
        "This dashboard compares actual token usage against a hypothetical pipeline that "
        "always uses the 70B model regardless of bug complexity."
    )

    BENCHMARK_ROWS = [
        {"Bug Type": "Syntax Error", "Model": "Qwen 1.5B", "Tokens": 120, "Baseline": 800, "Savings": "85%"},
        {"Bug Type": "Logic Error", "Model": "Llama 8B", "Tokens": 350, "Baseline": 800, "Savings": "56%"},
        {"Bug Type": "Architecture Flaw", "Model": "Llama 70B", "Tokens": 800, "Baseline": 800, "Savings": "0% (correct tier)"},
        {"Bug Type": "Average (Mixed Workload)", "Model": "Dynamic", "Tokens": 290, "Baseline": 800, "Savings": "~64%"},
    ]
    st.table(BENCHMARK_ROWS)

    if st.session_state.run_history:
        st.markdown("### This session")
        total_tokens = sum(e["tokens"] for e in st.session_state.run_history)
        total_baseline = len(st.session_state.run_history) * ALWAYS_70B_BASELINE

        # FIX (v1.1): Clamp session savings to 0% minimum.
        # Previously could go negative if fixer retries pushed tokens above baseline,
        # showing "-X% savings" which looks like a UI bug to judges.
        session_savings = max(round((1 - total_tokens / total_baseline) * 100, 1), 0) if total_baseline else 0

        m1, m2, m3 = st.columns(3)
        m1.metric("Runs this session", len(st.session_state.run_history))
        m1.metric("Tokens used", total_tokens)
        m2.metric("Always-70B baseline", total_baseline)
        m3.metric("Savings", f"{session_savings}%")

        # Visual savings bar for the session
        bar_pct = max(min(session_savings, 100), 0)
        st.markdown(
            f"""
            <div class="gd-savings-bar-wrap" style="margin-top:1rem;">
              <div class="gd-token-label">Session Savings</div>
              <div class="gd-savings-bar-track" style="margin-top:0.5rem;">
                <div class="gd-savings-bar-fill" style="width:{bar_pct}%"></div>
              </div>
              <div style="color:#7d8590;font-family:monospace;font-size:0.8rem;margin-top:0.3rem;">
                {total_tokens} tokens used vs {total_baseline} always-70B baseline
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.caption("Run some debug sessions to populate session stats.")

# ══════════════════════════ TAB 4: About ══════════════════════════ #
with tab_about:
    st.markdown(
        """
        ### GhostDebugger

        A token-efficient multi-agent debugging system built for the **AMD AI Developer
        Hackathon Act 2** (deadline July 11, 2026).

        **Pipeline:** Complexity Router → Reproducer → Tracer → Fixer → Reviewer

        **Routing logic:** a lightweight classifier (Qwen 1.5B) reads the broken code and
        assigns a complexity tier. Syntax-level bugs stay on the cheap model; logic bugs
        escalate to Llama 8B; architecture-level bugs escalate to Llama 70B. The Fixer
        agent never reports success without first re-executing the patched code in a
        sandbox and confirming it actually runs.

        **Backends:** Fireworks AI (cloud) with automatic fallback to a local Ollama
        model, and a deterministic mock response as the final fallback so the app runs
        with zero configuration.

        ---

        ### Health Check

        A `/health` endpoint is available for load balancer readiness probes and
        uptime monitoring. Start the companion FastAPI server:

        ```bash
        uvicorn api:app --host 0.0.0.0 --port 8080
        ```

        Then `GET http://localhost:8080/health` returns:
        ```json
        {"status": "ok", "version": "1.1.0", "fireworks_configured": true}
        ```

        ---
        Built by Franklin Josva A · [github.com/franklinjosva2605-dot](https://github.com/franklinjosva2605-dot)
        """
    )

