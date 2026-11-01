FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless procps \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pyspark==4.2.0 delta-spark==4.4.1

WORKDIR /app