FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPHEREX_CACHE_DIR=/data/cache \
    PORT=7860

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spherex_viewer ./spherex_viewer
COPY frontend ./frontend
COPY scripts ./scripts

RUN mkdir -p /data/cache && chmod -R 777 /data
EXPOSE 7860

# Hugging Face Spaces, Render, Fly, Railway... all inject $PORT.
CMD ["sh", "-c", "uvicorn spherex_viewer.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
