# Dockerfile (RAÍZ del proyecto) — usado por Dokploy como una sola aplicación.
FROM python:3.13-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Dependencias primero (se cachean entre despliegues si no cambian)
COPY services/data-collector/requirements.txt /tmp/req/data-collector.txt
COPY services/strategy-engine/requirements.txt /tmp/req/strategy-engine.txt
COPY services/execution-engine/requirements.txt /tmp/req/execution-engine.txt
COPY services/risk-manager/requirements.txt /tmp/req/risk-manager.txt
COPY services/monitoring/requirements.txt /tmp/req/monitoring.txt
RUN pip install --no-cache-dir --root-user-action=ignore \
    -r /tmp/req/data-collector.txt \
    -r /tmp/req/strategy-engine.txt \
    -r /tmp/req/execution-engine.txt \
    -r /tmp/req/risk-manager.txt \
    -r /tmp/req/monitoring.txt

# Código
COPY shared/ /app/shared/
COPY services/ /app/services/
COPY backtesting/ /app/backtesting/
COPY scripts/ /app/scripts/
COPY infra/ /app/infra/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

# "all" arranca los 5 servicios supervisados (scripts/run_all.py).
# También puedes poner un solo servicio, p.ej. SERVICE_NAME=data-collector.
ENV SERVICE_NAME=all

CMD ["sh", "-c", "if [ \"$SERVICE_NAME\" = \"all\" ]; then exec python /app/scripts/run_all.py; else exec python /app/services/${SERVICE_NAME}/src/main.py; fi"]
