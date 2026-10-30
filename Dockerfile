
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    DBUS_SESSION_BUS_ADDRESS=/dev/null


RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      curl wget gnupg ca-certificates \
      xvfb chromium chromium-driver \
      jq \
      build-essential \
      gcc \
      g++ \
      libc6-dev \
 && rm -rf /var/lib/apt/lists/*


ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver


ENV PYTHONPATH=/app
WORKDIR /app

COPY app/requirements.txt ./requirements.txt
RUN pip3 --timeout=300 --no-cache-dir install -r requirements.txt
RUN pip3 install --no-cache-dir playwright && \
    playwright install --with-deps chromium firefox

COPY app/ ./

COPY app/docker_run.sh ./
RUN sed -i 's/\r$//' docker_run.sh && \
    chmod +x docker_run.sh && \
    ls -la docker_run.sh

# 7) Entrypoint with explicit shell
# ENTRYPOINT ["/bin/bash", "/app/docker_run.sh"]
ENTRYPOINT ["bash", "/app/docker_run.sh"]