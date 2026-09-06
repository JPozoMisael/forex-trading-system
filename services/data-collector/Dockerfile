FROM python:3.12-slim

WORKDIR /app

# Dependencias del sistema mínimas (psycopg2 las necesita)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/data-collector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/data-collector/src/ ./src/
COPY shared/ /app/shared/

ENV PYTHONPATH=/app

CMD ["python", "-u", "src/main.py"]