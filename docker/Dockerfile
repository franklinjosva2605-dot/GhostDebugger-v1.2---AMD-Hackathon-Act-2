# GhostDebugger — containerized deployment
#
# This image runs the Streamlit UI by default. Sandbox code execution
# happens inside THIS container's own subprocess sandbox (see
# sandbox/executor.py: AST import allowlist + resource limits + timeouts).
#
# SECURITY NOTE: this container, as built, still needs outbound network
# access (to call Fireworks AI) and a writable filesystem (for Streamlit's
# own runtime + temp dirs), so it does NOT run with --read-only or
# --network none by default. The app container and the "execute untrusted
# code" boundary are the same process here, which is fine for a hackathon
# demo but is exactly the gap the security assessment flags (Finding 1,
# Finding 4, Finding 5). If you take this further than a demo, split the
# sandbox into its own disposable container with no network and a
# read-only root filesystem, and have this app container talk to it over
# a narrow internal API instead of calling subprocess.run() in-process.
#
# Recommended hardened run flags for THIS container (still not a substitute
# for splitting out the sandbox, but cheap and worth doing regardless):
#   docker run -p 8501:8501 --env-file .env \
#     --cap-drop ALL --security-opt no-new-privileges \
#     --memory 512m --cpus 1 \
#     ghostdebugger
#
# Build:  docker build -t ghostdebugger -f docker/Dockerfile .
# Run:    see flags above

FROM python:3.11-slim

# Keep image lean; only what's needed to run + sandbox-execute Python snippets.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY agents/ ./agents/
COPY sandbox/ ./sandbox/
COPY ui/ ./ui/
COPY cli.py .
COPY .env.example .

# Run as a non-root user — the sandbox executor already restricts memory/CPU/procs,
# but an unprivileged container user is cheap defense-in-depth on top of that.
RUN useradd --create-home --shell /bin/bash ghost
USER ghost

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import requests; requests.get('http://localhost:8501/_stcore/health').raise_for_status()" || exit 1

ENTRYPOINT ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
