FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/strategy-engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/strategy-engine/src/ ./src/
COPY shared/ /app/shared/

ENV PYTHONPATH=/app

CMD ["python", "-u", "src/main.py"]
