# legalPrivacy in a container: for a home server, a NAS or a Raspberry Pi (arm64).
# Includes Chromium (real-browser noise) and Tor (optional onion noise).
# See docs/DOCKER.md. Build:  docker build -t legalprivacy .
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-liberation ca-certificates tor \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
RUN pip install --no-cache-dir "requests[socks]>=2.31" "PyYAML>=6.0" "playwright>=1.45"
COPY legalprivacy ./legalprivacy
COPY data ./data
COPY profiles ./profiles

# Settings live in /data (a volume). The dashboard listens inside the container; publish it to
# 127.0.0.1 on the host only. The container is Chromium's sandbox, so its own sandbox is off.
ENV LEGALPRIVACY_HOME=/data \
    LEGALPRIVACY_BIND=0.0.0.0 \
    LEGALPRIVACY_BROWSER=/usr/bin/chromium \
    LEGALPRIVACY_NO_SANDBOX=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home legalprivacy && mkdir -p /data && chown legalprivacy /data
USER legalprivacy
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=5m --timeout=10s --start-period=1m \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8765), 5)" || exit 1
CMD ["python", "-m", "legalprivacy", "--no-browser", "--port", "8765"]
