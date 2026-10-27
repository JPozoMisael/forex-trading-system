FROM python:3.12-slim

WORKDIR /app

# libgomp1: OpenMP, lo necesita LightGBM
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libgomp1 \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/strategy-engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/strategy-engine/src/ ./src/
COPY shared/ /app/shared/
COPY config.yaml /app/config.yaml

ENV PYTHONPATH=/app

CMD ["python", "-u", "src/main.py"]
