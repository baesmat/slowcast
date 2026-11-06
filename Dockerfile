FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV SC_HOST=0.0.0.0 \
    SC_PORT=8330 \
    SC_DB_PATH=/data/slowcast.db \
    SC_DATA_DIR=/data

VOLUME ["/data"]
EXPOSE 8330

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get("SC_PORT","8330")}/healthz').read()"

CMD ["slowcast"]
