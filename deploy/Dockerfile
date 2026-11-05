# The Fix-It Lens app image for compose.yaml: the phone PWA built in a node stage, the server on python:3.12-slim.
# No key, no manual and no private data are in the image; /data is the private folder (a volume).

FROM node:22-slim AS phone
WORKDIR /src/phone
COPY phone/package.json phone/package-lock.json ./
RUN npm ci --no-audit --no-fund --silent
COPY contracts /src/contracts
COPY rules /src/rules
COPY phone/ ./
RUN npm run build && rm -rf node_modules

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv
# The video witness builds its clips with ffmpeg: without it a step falls back to one frame.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 \
    FIXIT_PRIVATE=/data FIXIT_CALLS=replay FIXIT_VAPID=/data/secrets/vapid.pem
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agent agent
COPY app app
COPY calls calls
COPY cards cards
COPY config config
COPY contracts contracts
COPY deploy deploy
COPY integrations integrations
COPY fixtures fixtures
COPY ingest ingest
COPY local local
COPY rules rules
COPY schema schema
COPY --from=phone /src/phone/dist phone/dist
RUN useradd --uid 10001 --home-dir /data --no-create-home fixit && mkdir -p /data && chown fixit /data
USER fixit
VOLUME /data
CMD ["python", "deploy/start.py"]
