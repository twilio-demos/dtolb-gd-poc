# Builds on the twl host (a Raspberry Pi), so the target is linux/arm64.
# python:3.12-slim publishes an arm64 variant; the SDK needs >=3.10.
FROM python:3.12-slim

# git is needed at BUILD time only: the TAC SDK is a git dependency
# (see pyproject.toml), so the Pi must be able to reach github.com.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv is the project's package manager (README uses `uv sync`). Multi-arch image.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so editing app.py doesn't re-resolve the whole tree.
# --frozen: build exactly what uv.lock pins, never re-resolve. That pin is
# load-bearing — it fixes the TAC git dep to one commit, and the knowledge-tool
# workaround (KNOWN-ISSUES #1) depends on that version's Pydantic return type.
# --no-install-project: this repo is flat modules (app.py/web.py/events.py),
# not an installable package, so only install dependencies.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .

# Use the venv uv just built.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# The single routed port. Traefik forwards here; it also upgrades the
# ConversationRelay WebSocket (/ws) over the same port — uvicorn[standard]
# supplies the `websockets` implementation.
EXPOSE 8000

# `uvicorn app:app` works because TACFastAPIServer is constructed at module
# scope (KNOWN-ISSUES #7) — under the old __main__-only wiring this would have
# served the landing page with no /twiml route and no websocket.
# --host 0.0.0.0: Traefik cannot reach a loopback-only bind.
#
# NOTE: import fails fast if TWILIO_VOICE_PUBLIC_DOMAIN is unset while a voice
# channel exists (tac/server/fastapi_server.py:142). The container therefore
# crash-loops until `twl env set` provides the full environment — that is the
# intended signal, not a bug.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
