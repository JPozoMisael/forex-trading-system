# CLAUDE.md

Guía para Claude Code en este repositorio. Responde al usuario en **español**.

## Qué es

Sistema de trading Forex algorítmico en Python, orientado a eventos, con microservicios que se comunican por **Redis Streams** y guardan estado en **TimescaleDB**. Fuente de datos: **OANDA** (API REST, corre en Linux/Docker) o **MetaTrader 5** (broker XM, solo Windows con el terminal abierto). El README.md tiene la guía de usuario completa.

## Comandos

```powershell
# Entorno (Windows)
python -m venv .venv
.venv\Scripts\pip install -r requirements-local.txt
copy .env.example .env            # completar credenciales; .env NUNCA se commitea

# Tests (deben pasar antes de cada commit)
.venv\Scripts\python -m pytest tests -v
.venv\Scripts\python -m pytest tests/test_risk_manager.py -v   # un archivo

# Infra local (Postgres + Redis + Grafana)
docker compose -f docker-compose-dev.yml up -d

# Diagnóstico del entorno de esta computadora (no envía órdenes ni muestra credenciales)
.venv\Scripts\python scripts\diagnostico.py --guardar   # guarda en docs/diagnosticos/

# Verificaciones (no envían órdenes)
.venv\Scripts\python scripts\check_feed.py    # fuente OANDA
.venv\Scripts\python scripts\check_mt5.py     # terminal MT5/XM

# Arrancar todo con MT5 en Windows
powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1

# Todo en Docker (servidor/Dokploy, solo con MARKET_DATA_PROVIDER=oanda)
docker compose up -d --build
# Dokploy usa el Dockerfile raíz: los 5 servicios en un contenedor vía scripts/run_all.py

# Backtest
.venv\Scripts\python backtesting\src\run_backtest.py --pair EUR_USD --strategy ma_crossover --source synthetic
```

Un servicio se corre nativo con `.venv\Scripts\python services\<servicio>\src\main.py`.

## Arquitectura

Flujo: `data-collector` → `strategy-engine` → `risk-manager` → `execution-engine` → broker; `monitoring` escucha todo y alerta.

| Servicio | Entrada | Salida | Archivos clave |
|---|---|---|---|
| data-collector | broker (OANDA/MT5) | tabla `candles` + stream `market:candle:<PAR>` | `main.py`, `oanda_client.py`, `db_writer.py` |
| strategy-engine | `market:candle:*` + historia de BD | `signals:new` | `strategies/*.py`, `indicators.py` |
| risk-manager | `signals:new` + `state:account` + `state:symbol:*` | reserva orden `pending` en BD + `orders:approved` | `rules.py`, `position_sizing.py` |
| execution-engine | `orders:approved` | `orders:executed`, `orders:closed`; publica `state:account` cada 5 s, reconcilia cada 10 s | `brokers.py`, `order_manager.py`, `reconciliation.py` |
| monitoring | todos los streams | Telegram / email / health check | `telegram_alerts.py`, `email_alerts.py`, `health_check.py` |

Código compartido en `shared/`: `config.py` (toda la config sale de aquí vía `settings`), `bus.py` (`EventBus` sobre Redis Streams con grupos de consumidores), `models.py` (Pydantic: `Signal`, `OrderRequest`, `AccountSummary`, `SymbolSpec`…), `fx.py` (pips y conversión a USD), `mt5_client.py`, `market_client.py` (interfaz `MarketDataClient`).

Ciclo de una orden en BD: `pending` → `submitting` → `filled` → `closed` (o `rejected` / `cancelled`). Esquema en `infra/postgres/init.sql`; cambios de esquema van como nuevo archivo en `infra/postgres/migrations/` (siguiente: `002_...sql`).

### Convenciones

- Cada `main.py` agrega la raíz del repo a `sys.path` e importa los módulos de su `src/` sin prefijo (`from rules import ...`). `tests/conftest.py` replica eso agregando cada `services/*/src` al path.
- Código, comentarios, logs y mensajes en **español**. Logs con `shared.logger.get_logger(__name__)`.
- No leer `os.getenv` fuera de `shared/config.py`; agregar el campo a `Settings` y documentarlo en `.env.example`.
- Nuevo broker de datos → implementar `MarketDataClient` y añadir rama en `build_market_client()` (data-collector). Nuevo broker de ejecución → implementar `Broker` en `services/execution-engine/src/brokers.py` y añadirlo en `build_broker()`.
- Nueva estrategia → subclase de `BaseStrategy` en `services/strategy-engine/src/strategies/`, registrarla en la lista de `strategy-engine/src/main.py` y en `backtesting/src/run_backtest.py`; añadir test en `tests/test_strategies.py`.
- Horas siempre en UTC (MT5 entrega hora del servidor; `mt5_client.py` la convierte).

## Reglas de seguridad (no romper)

- **Fail-closed**: si falta información (BD caída, snapshot de cuenta viejo, spec de símbolo ausente) el risk-manager **rechaza**. No convertir rechazos en aprobaciones "por defecto".
- El tamaño de lote nunca se redondea hacia arriba.
- `execution-engine` es el **único** servicio que envía órdenes al broker y corre en un solo hilo (MT5 no es thread-safe).
- Ejecución idempotente: una orden no debe enviarse dos veces (comentario `fx#<order_id>` en MT5 para recuperarla tras reinicio).
- `DRY_RUN=true` por defecto. Con cuenta MT5 **real** el sistema se niega a arrancar salvo `ALLOW_LIVE_TRADING=true`. Nunca cambiar estos defaults ni activar trading real sin pedirlo explícitamente el usuario.
- Nunca commitear `.env` ni credenciales.

## Estado del proyecto y continuidad entre computadoras

El estado ya no se guarda aquí. Vive en `docs/`:

| Archivo | Para qué |
|---|---|
| `docs/BITACORA.md` | Qué se hizo en cada sesión y cuál es el **siguiente paso**. Leer primero. |
| `docs/DIAGNOSTICO.md` | Estado técnico del código: qué funciona, problemas P1..Pn, riesgos. |
| `docs/PLAN_INTEGRACION_COLAB.md` | Hoja de ruta para portar el script de Google Colab (`research/colab/`). |
| `docs/diagnosticos/<fecha>_<equipo>.md` | Informe del entorno de cada computadora (lo genera `scripts/diagnostico.py`). |

**Al empezar una sesión:** `git pull`, leer `docs/BITACORA.md` (entrada más reciente) y correr
`python scripts/diagnostico.py --sin-tests` para ver qué le falta a esta computadora.

**Al terminar una tarea relevante:** añadir una entrada arriba en `docs/BITACORA.md`, actualizar la tabla de
problemas de `docs/DIAGNOSTICO.md` si cambió, y proponer al usuario hacer commit + push.

## Trabajar desde otra computadora

1. `git clone git@github.com:JPozoMisael/forex-trading-system.git` (o `git pull` si ya existe).
2. Crear `.venv` e instalar dependencias (ver Comandos). En Linux/Mac: `python3 -m venv .venv && .venv/bin/pip install -r requirements-local.txt` quitando la línea `MetaTrader5`.
3. Copiar `.env.example` a `.env` y rellenar credenciales (no están en git; guardarlas en un gestor de contraseñas).
4. `python scripts/diagnostico.py --guardar` → revisar el resumen y commitear el informe.
