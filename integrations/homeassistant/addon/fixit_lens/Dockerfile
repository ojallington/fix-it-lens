# The Fix-It Lens add-on image. The Supervisor builds it from the folder stage.py writes: this file, config.yaml and
# the app's tracked source in src/ (no key, no manual, no private data). /data is the private folder, kept by the
# Supervisor and in Home Assistant's backups.
FROM node:22-slim AS phone
WORKDIR /src/phone
COPY src/phone/package.json src/phone/package-lock.json ./
RUN npm ci --no-audit --no-fund --silent
COPY src/contracts /src/contracts
COPY src/rules /src/rules
COPY src/phone/ ./
RUN npm run build && rm -rf node_modules

FROM python:3.12-slim
ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=0.1.0
LABEL io.hass.type="addon" io.hass.arch="${BUILD_ARCH}" io.hass.version="${BUILD_VERSION}" \
      io.hass.name="Fix-It Lens"
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv
# The video witness builds its clips with ffmpeg: without it a step falls back to one frame.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 HOME=/data \
    FIXIT_PRIVATE=/data FIXIT_CALLS=replay FIXIT_VAPID=/data/secrets/vapid.pem
WORKDIR /app
COPY src/pyproject.toml src/uv.lock src/.python-version src/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./
COPY --from=phone /src/phone/dist phone/dist
CMD ["python", "deploy/ha_addon.py"]
