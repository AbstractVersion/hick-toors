# hick-toors — local container build (FastAPI + static Leaflet UI)
#
# Runtime note: Wikiloc's Cloudflare front end answers 403 to some TLS stacks. Debian's Python
# (python:3.12-slim, OpenSSL 3.0.x) and older uv Python builds were refused, while uv's
# python-build-standalone 3.10.20 (OpenSSL 3.5.x, the same runtime used outside Docker) is
# accepted — so the image uses exactly that interpreter instead of the distro Python.
FROM debian:bookworm-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /uvx /bin/

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 hick

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN uv python install 3.10.20

WORKDIR /app

# Dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python 3.10.20

COPY app ./app
COPY static ./static

# Search cache (hikes, stays, geocoding) and saved trips (SQLite) live here; compose mounts volumes on them.
RUN mkdir -p /app/.cache /app/data && chown -R hick /app/.cache /app/data
USER hick

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
