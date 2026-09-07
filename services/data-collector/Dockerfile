# services/data-collector/Dockerfile
FROM python:3.13-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copiar shared primero (dependencia común)
COPY shared/ /app/shared/

# Copiar solo el data-collector
COPY services/data-collector/ /app/services/data-collector/

# Copiar backtesting (si es necesario)
COPY backtesting/ /app/backtesting/

# Instalar dependencias
RUN pip install --no-cache-dir -r /app/services/data-collector/requirements.txt

# Agregar /app al PYTHONPATH
ENV PYTHONPATH=/app

# Puerto (opcional)
EXPOSE 8000

# Comando para ejecutar el data-collector
CMD ["python", "/app/services/data-collector/src/main.py"]