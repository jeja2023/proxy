FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY panel/*.py ./
COPY panel/static ./static
COPY core ./core

RUN groupadd --system --gid 10001 panel \
    && useradd --system --uid 10001 --gid panel --home-dir /app --shell /usr/sbin/nologin panel \
    && chown -R 10001:panel /app

ENV PYTHONPATH=/app
ENV PROXY_REPO_ROOT=/app
ENV CLASH_API_URL=http://proxy-bridge:9020
EXPOSE 8080

USER 10001:panel

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
