# Built on the twl host (a Raspberry Pi), so the target is linux/arm64.
FROM python:3.12-slim

# git is a build-time requirement: the TAC SDK is a git dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first so editing source doesn't re-resolve the tree.
# --frozen pins the TAC git dependency to the commit in uv.lock; the knowledge
# tool workaround in app.py depends on that version's return type.
# --no-install-project: this repo is flat modules, not an installable package.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Traefik routes here, and upgrades the ConversationRelay WebSocket on the same
# port. uvicorn[standard] supplies the websockets implementation.
EXPOSE 8000

# --proxy-headers/--forwarded-allow-ips: trust the proxy's X-Forwarded-* headers.
# See TrustProxyHTTPS in app.py, which additionally forces the https scheme.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
