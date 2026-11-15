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

# Compiled once here instead of at every start (PYTHONDONTWRITEBYTECODE stops it at run time).
RUN python -m compileall -q spherex_viewer scripts \
 && mkdir -p /data/cache && chmod -R 777 /data
EXPOSE 7860

# Hugging Face Spaces, Render, Fly, Railway... all inject $PORT.
# Keep idle connections open longer than the host's load balancer does: with
# uvicorn's 5 s default the proxy reuses connections the server has just
# closed, and some requests fail with a 502 at random.
CMD ["sh", "-c", "uvicorn spherex_viewer.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1 --timeout-keep-alive 120"]
