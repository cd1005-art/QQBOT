FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    QQBOT_BIND=0.0.0.0 \
    QQBOT_CONFIG=/app/config/config.toml

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY qqbot ./qqbot
COPY web ./web
COPY config.example.toml ./config.example.toml
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /app/data /app/config

EXPOSE 18900 18901

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:18900/qqbot/webhook', timeout=3).status==200 else 1)"

ENTRYPOINT ["/entrypoint.sh"]
