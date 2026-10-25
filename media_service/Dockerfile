# Build context: repository root (docker-compose.yml sets it).
FROM node:20-bookworm-slim AS node

FROM python:3.11-slim-bookworm

# WITH_CUDA=1 adds the CUDA runtime libraries faster-whisper needs on GPU.
ARG WITH_CUDA=0

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    CONFIG_DIR=/config \
    ASSETS_DIR=/assets \
    REMOTION_DIR=/remotion \
    HF_HOME=/root/.cache/huggingface \
    LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib

# ffmpeg + the shared libraries Remotion's headless Chromium needs
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates fonts-dejavu-core \
        libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
        libasound2 libx11-xcb1 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Node.js for the Remotion CLI
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

WORKDIR /remotion
COPY remotion/package.json remotion/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY remotion/tsconfig.json remotion/remotion.config.ts ./
COPY remotion/src ./src
RUN npx remotion browser ensure

WORKDIR /app
COPY media_service/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt \
    && if [ "$WITH_CUDA" = "1" ]; then pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"; fi

COPY media_service/app ./app
COPY media_service/scripts ./scripts
COPY media_service/tests ./tests
COPY media_service/pytest.ini ./pytest.ini

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
