FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so edits to source do not invalidate the layer.
COPY pyproject.toml README.md ./
COPY taxwatch ./taxwatch
RUN pip install --no-cache-dir .

COPY config ./config

EXPOSE 8000

CMD ["taxwatch", "serve", "--host", "0.0.0.0", "--port", "8000"]
