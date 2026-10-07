FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tor \
        build-essential \
        libssl-dev \
        libffi-dev \
        libjpeg62-turbo-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Render Secret Files are readable by group 1000; keep the app non-root.
RUN groupadd --gid 1000 render-secrets \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && usermod --append --groups 1000 app

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY --chown=app:app . /app
RUN chmod +x /app/start.sh \
    && mkdir -p /tmp/tor-data \
    && chown app:app /tmp/tor-data

USER app
ENTRYPOINT ["/app/start.sh"]
