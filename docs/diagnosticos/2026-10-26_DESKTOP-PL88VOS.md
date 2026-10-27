# Diagnóstico de entorno - DESKTOP-PL88VOS - 2026-10-26 21:49 -0500

## Sistema

- [i] Equipo: DESKTOP-PL88VOS | Windows 10 | Python 3.13.14
- [i] Entorno virtual: NO (usa .venv) (C:\Users\Jeff\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe)

## Dependencias (requirements-local.txt)

- [v] MetaTrader5 5.0.5735
- [v] numpy 2.2.3
- [v] pandas 2.2.3
- [v] psycopg2-binary 2.9.12
- [v] pydantic 2.9.2
- [v] python-dotenv 1.0.1
- [v] redis 5.0.8
- [v] requests 2.32.3
- [v] tenacity 9.0.0
- [v] oandapyV20 0.7.2
- [v] matplotlib 3.10.0
- [v] tabulate 0.9.0
- [v] pytest 9.1.1
- [x] Faltan paquetes: fakeredis -> pip install -r requirements-local.txt

## Git

- [i] Rama main | último commit: 1c149af fix(oanda): mostrar las cuentas válidas del token cuando falla OANDA_ACCOUNT_ID (13 days ago)
- [i] Cambios sin commitear (6):
    D  .coverage
     M .gitignore
    ?? CLAUDE.md
    ?? docs/
    ?? research/
    ?? scripts/diagnostico.py
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

- [x] No se pudo conectar al terminal ((-6, 'Terminal: Authorization failed')). Detalle: python scripts/check_mt5.py

## Resumen

Problemas encontrados:
1. Faltan paquetes: fakeredis -> pip install -r requirements-local.txt
1. No existe .env -> copia .env.example a .env y complétalo
1. Docker no responde (¿Docker Desktop cerrado o no instalado?)
1. No hay nada escuchando en localhost:5432
1. No responde en localhost:6379 (Error 10061 connecting to localhost:6379. No se puede establecer una conexión ya que el equipo de destino denegó expresamente dicha conexión.)
1. No se pudo conectar al terminal ((-6, 'Terminal: Authorization failed')). Detalle: python scripts/check_mt5.py
