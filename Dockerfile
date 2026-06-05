# sisoul Dockerfile — alpha-tier zero-dep try-it-out container.
# Build: docker build -t sisoul:alpha .
# Run:   docker run -p 9876:9876 -v ~/.sisoul:/root/.sisoul sisoul:alpha
#        then: open http://localhost:9876/sisoul/health

FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for libsodium + cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libsodium-dev \
        libffi-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /build/
COPY src/ /build/src/

RUN pip install --no-cache-dir build && \
    python -m build --wheel && \
    pip install --no-cache-dir dist/sisoul-*.whl


# --- runtime ---
FROM python:3.12-slim

LABEL org.opencontainers.image.title="sisoul"
LABEL org.opencontainers.image.description="Decentralized P2P AI agent protocol (alpha v1.0)"
LABEL org.opencontainers.image.source="https://github.com/sisoul/sisoul"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.version="1.0.0-alpha"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libsodium23 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r sisoul && useradd -r -g sisoul -m -d /home/sisoul sisoul

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/sisoul /usr/local/bin/sisoul

USER sisoul
WORKDIR /home/sisoul

ENV SISOUL_VAULT=/home/sisoul/.sisoul
ENV SISOUL_DAEMON_PORT=9876
ENV PYTHONUNBUFFERED=1

# Init vault non-interactive on first run (mounted volume preserves between runs)
RUN sisoul --version

EXPOSE 9876

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fs http://127.0.0.1:9876/sisoul/health || exit 1

# Default: start daemon. Override with docker run sisoul:alpha <other-command>
CMD ["sisoul", "daemon", "--host", "0.0.0.0", "--port", "9876"]
