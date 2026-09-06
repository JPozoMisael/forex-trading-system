FROM python:3.13-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copiar todo el proyecto
COPY shared/ /app/shared/
COPY services/ /app/services/
COPY backtesting/ /app/backtesting/

# Instalar dependencias de todos los servicios
RUN pip install --no-cache-dir -r /app/services/data-collector/requirements.txt \
    && pip install --no-cache-dir -r /app/services/strategy-engine/requirements.txt \
    && pip install --no-cache-dir -r /app/services/execution-engine/requirements.txt \
    && pip install --no-cache-dir -r /app/services/risk-manager/requirements.txt \
    && pip install --no-cache-dir -r /app/services/monitoring/requirements.txt

# 🔧 IMPORTANTE: Agregar /app al PYTHONPATH
ENV PYTHONPATH=/app

# Variable para elegir qué servicio ejecutar
ENV SERVICE_NAME=data-collector

# Comando por defecto
CMD ["sh", "-c", "python /app/services/${SERVICE_NAME}/src/main.py"]