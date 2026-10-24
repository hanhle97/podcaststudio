# Build context: repository root (docker-compose.yml sets it).
FROM python:3.11-slim-bookworm

# WITH_CUDA=1 adds the CUDA runtime libraries faster-whisper needs on GPU.
ARG WITH_CUDA=0

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    CONFIG_DIR=/config \
    ASSETS_DIR=/assets \
    REMOTION_DIR=/remotion

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

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
