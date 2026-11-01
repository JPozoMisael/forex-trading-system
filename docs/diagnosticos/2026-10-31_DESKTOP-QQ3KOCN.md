# Diagnóstico de entorno - DESKTOP-QQ3KOCN - 2026-10-31 23:22 -0500

## Sistema

- [i] Equipo: DESKTOP-QQ3KOCN | Windows 11 | Python 3.13.14
- [v] Entorno virtual: activo (D:\forex-trading-system\.venv\Scripts\python.exe)

## Dependencias (requirements-local.txt)

- [v] MetaTrader5 5.0.6231
- [v] numpy 2.2.3
- [v] pandas 2.2.3
- [v] psycopg2-binary 2.9.13
- [v] pydantic 2.9.2
- [v] python-dotenv 1.0.1
- [v] redis 5.0.8
- [v] requests 2.32.3
- [v] tenacity 9.0.0
- [v] oandapyV20 0.7.2
- [v] matplotlib 3.10.0
- [v] tabulate 0.9.0
- [v] dukascopy-python 4.0.1
- [v] pyarrow 26.0.0
- [v] PyYAML 6.0.3
- [v] lightgbm 4.7.0
- [v] scikit-learn 1.9.1
- [v] joblib 1.6.0
- [v] pytest 9.2.0
- [v] fakeredis 2.40.0

## Git

- [i] Rama main | último commit: 5b1a65f fix(reporte): todas las temporalidades se comparan contra el precio actual (2 days ago)
- [v] Árbol de trabajo limpio
- [v] Sincronizado con origin

## .env (solo se indica si cada variable tiene valor)

- [x] No existe .env -> copia .env.example a .env y complétalo

## Docker

- [x] Docker no responde (¿Docker Desktop cerrado o no instalado?)

## TimescaleDB

- [x] No hay nada escuchando en localhost:5432

## Redis

- [x] No responde en localhost:6379 (Error 10061 connecting to localhost:6379. No se puede establecer una conexión ya que el equipo de destino denegó expresamente dicha conexión.)

## MetaTrader 5

- [x] No se pudo conectar al terminal ((-10003, 'IPC initialize failed, MetaTrader 5 x64 not found')). Detalle: python scripts/check_mt5.py

## Tests (pytest)

- [v] 146 passed, 14 warnings in 240.80s (0:04:00)

## Resumen

Problemas encontrados:
1. No existe .env -> copia .env.example a .env y complétalo
1. Docker no responde (¿Docker Desktop cerrado o no instalado?)
1. No hay nada escuchando en localhost:5432
1. No responde en localhost:6379 (Error 10061 connecting to localhost:6379. No se puede establecer una conexión ya que el equipo de destino denegó expresamente dicha conexión.)
1. No se pudo conectar al terminal ((-10003, 'IPC initialize failed, MetaTrader 5 x64 not found')). Detalle: python scripts/check_mt5.py
