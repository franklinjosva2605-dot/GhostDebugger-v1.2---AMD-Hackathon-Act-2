# 👻 GhostDebugger

A token-efficient multi-agent AI debugging system built for the **AMD AI Developer Hackathon Act 2** (deadline July 11, 2026).

GhostDebugger routes broken code to the cheapest model capable of fixing it, then deploys five specialized agents to reproduce, trace, fix, and explain the bug — outputting a senior-developer-quality explanation with quantified token savings.

```
Complexity Router → Reproducer → Tracer → Fixer → Reviewer
   (Qwen 1.5B)      (sandbox)   (tiered)  (tiered)  (tiered)
```

## Quick start (no API key needed to try it)

```bash
# 1. Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run it — works immediately with a mock LLM backend, no key required
streamlit run ui/app.py
```

Open the link Streamlit prints (usually `http://localhost:8501`). Check the box to load the sample buggy snippet and click **Run GhostDebugger** to see the full pipeline execute. Without an API key you'll get real sandbox execution (real Python errors, real tracebacks) and clearly-labeled mock text in place of LLM reasoning — once you add a key, everything becomes real model output with zero code changes.

## Adding your Fireworks AI key

```bash
cp .env.example .env
# then edit .env and paste in:
# FIREWORKS_API_KEY=your_key_here
```

Get a key at [fireworks.ai](https://fireworks.ai). Restart the app after adding it.

## CLI usage (no UI)

```bash
python cli.py path/to/buggy_script.py
python cli.py path/to/buggy_script.py --error "paste traceback here"
echo 'print(1/0)' | python cli.py -
```

## Running with Docker

```bash
cp .env.example .env   # fill in FIREWORKS_API_KEY first
docker compose -f docker/docker-compose.yml up --build
```

This also starts a local Ollama container as an offline fallback. If you have an AMD GPU, uncomment the `devices` section in `docker/docker-compose.yml` to enable ROCm acceleration for Ollama.

To build/run without compose:
```bash
docker build -t ghostdebugger -f docker/Dockerfile .
docker run -p 8501:8501 --env-file .env ghostdebugger
```

## Project structure

```
ghostdebugger/
├── core/
│   ├── llm_client.py      # 4-tier LLM routing: Fireworks AI -> Ollama -> mock fallback
│   └── orchestrator.py    # Wires all 5 agents into the full pipeline
├── agents/
│   ├── router_agent.py    # Agent 1: classifies bug complexity (syntax/logic/architecture)
│   ├── reproducer_agent.py # Agent 2: executes code, captures real traceback
│   ├── tracer_agent.py     # Agent 3: root-cause analysis from the traceback
│   ├── fixer_agent.py      # Agent 4: generates + sandbox-verifies a patch
│   └── reviewer_agent.py   # Agent 5: senior-developer-style write-up
├── sandbox/
│   └── executor.py        # Subprocess sandbox: timeouts, memory limits, AST-based import allowlist
├── ui/
│   └── app.py              # Streamlit UI — Debug / Pipeline Detail / Token Savings / About
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── tests/
│   ├── test_sandbox.py
│   └── test_router_agent.py
├── cli.py
├── requirements.txt
└── .env.example
```

## How the token savings work

Every bug is classified by the Router (cheap Qwen 1.5B call, ~50 tokens) into one of three tiers, which determines which model handles tracing and fixing:

| Bug Type | Model | Typical Tokens | vs. Always-70B | Savings |
|---|---|---|---|---|
| Syntax Error | Qwen 1.5B | ~120 | ~800 | 85% |
| Logic Error | Llama 8B | ~350 | ~800 | 56% |
| Architecture Flaw | Llama 70B | ~800 | ~800 | 0% (correct answer) |
| **Average (mixed)** | Dynamic | **~290** | ~800 | **~64%** |

The **Token Savings** tab in the UI tracks this live against your actual session usage.

## Design notes

- **No hallucinated fixes**: the Fixer agent always re-executes its proposed patch in the sandbox before reporting success. If the patch still fails after 2 attempts, the UI clearly flags it as unverified rather than claiming a fix that doesn't work.
- **Graceful degradation**: missing API key → tries Ollama → falls back to a clearly-labeled mock response. The pipeline structure, sandboxing, and routing logic are fully exercised even with zero credentials.
- **Sandbox isolation**: subprocess-level isolation with memory/CPU/process limits and an **AST-based import allowlist** (not a string-matching denylist — see [`SECURITY.md`](SECURITY.md) for why that distinction matters and what's still open). Sufficient for a hackathon demo and personal use; not yet hardened enough for a public multi-tenant service running anonymous untrusted code.
- **Secrets hygiene**: `.env` is gitignored from the first commit (see Lessons Learned in the portfolio — never repeat the leaked-key incident).

See [`SECURITY.md`](SECURITY.md) for the full security review findings, what's been fixed, and what's explicitly still open if you take this beyond a demo.

## Running tests

```bash
python tests/test_sandbox.py
python tests/test_router_agent.py
```

---

Built by Franklin Josva A · [github.com/franklinjosva2605-dot](https://github.com/franklinjosva2605-dot) · "Build quietly. Win loudly."
