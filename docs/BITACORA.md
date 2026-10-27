# Bitácora de trabajo

Registro corto de cada sesión, para retomar desde cualquier computadora. **Lo más reciente arriba.**
Formato: fecha · equipo · qué se hizo · qué quedó pendiente / siguiente paso.

---

## 2026-10-27 (tarde) · DESKTOP-RC7UDV1

- **OANDA no acepta clientes de Ecuador** (la solicitud de cuenta redirige a FTMO). Se descarta como fuente y como broker.
- **Fase 3 hecha, datos de Dukascopy** (gratis, sin cuenta): `load_from_dukascopy()` en `backtesting/src/data_loader.py`. Calcula el precio medio desde bid/ask (convención del backtester), guarda el spread real por vela, solo velas cerradas y caché incremental en `data/dukascopy_<PAR>_<TF>.parquet`.
  - `python scripts/descargar_datos.py` descarga y revisa la calidad. EUR_USD H1: 37.416 velas (2020-10-27 → hoy) en 26 s. Los únicos huecos son Navidad y Año Nuevo.
  - `run_backtest.py` usa `--source dukascopy --granularity H1` por defecto.
- **Línea base real:** `ma_crossover` en 6 años de EUR_USD H1 con spread 1.0 + slippage 0.3 + comisión $7/lote: **−66.9 %**, profit factor 0.82, 788 operaciones. No tiene ventaja.
- Dokploy: los servicios caen porque `REDIS_HOST=redis` no existe (crear el servicio Redis en el proyecto y usar su *Internal Host* + `REDIS_PASSWORD`) y porque las credenciales de OANDA eran el texto de ejemplo. Aunque se arregle Redis, el data-collector no tendrá fuente hasta implementar `MARKET_DATA_PROVIDER=dukascopy` (P10). Mientras tanto, dejar la aplicación detenida.
- **Siguiente paso:** fase 4 (`validation.py`, walk-forward + permutación + bootstrap) y fase 5 (`MLStrategy`).

## 2026-10-27 · DESKTOP-RC7UDV1

- **Backtester con costos y ejecución realistas** (`backtesting/src/engine.py`, commit `9ddf2bf`): spread completo (compra al ask, vende al bid sobre velas de precio medio), slippage en entrada y en todas las salidas, comisión por lote, entrada a la **apertura de la vela siguiente** a la señal, SL si SL y TP caen en la misma vela (un gap llena a la apertura) y límite de pérdida diaria igual que el circuit breaker en vivo. CLI: `--slippage`, `--commission`, `--max-daily-loss`. Un test por regla en `tests/test_backtester_execution.py` (62 tests pasan).
- `infra/postgres/00_create_database.sql`: crea el usuario `forex` y la BD vacía en un Postgres propio (paso previo a `init_db.py`).
- Diagnóstico de este equipo: falta `.env`; Docker, Postgres, Redis y MT5 no disponibles.
- El plan del Colab y el del modelo predictivo se unieron en uno solo: ver "Fases (plan combinado)" en [PLAN_INTEGRACION_COLAB.md](PLAN_INTEGRACION_COLAB.md).
- **Siguiente paso:** crear `.env` con el token de OANDA (cuenta demo) y hacer la fase 3 (descargar ≥ 5 años de EUR_USD H1 a `data/`).

## 2026-10-27 · DESKTOP-PL88VOS

- **Base de datos v2** (`infra/postgres/init.sql`): funciona con PostgreSQL normal o con TimescaleDB, se puede ejecutar varias veces y fija UTC. Tiene restricciones, una segunda barrera de "un trade por par", `r_multiple` por orden, 4 tablas preparadas (`account_snapshots`, `risk_events`, `backtest_runs`, `backtest_trades`) y 7 vistas. Guía en `docs/BASE_DE_DATOS.md`.
- `scripts/init_db.py`: aplica el esquema a la BD del `.env` en una transacción (`--verificar` solo muestra el estado).
- Probado en PostgreSQL 16.4 temporal: BD vacía, segunda ejecución sin cambios, actualización v1→v2 con datos, y el código real (DBWriter, reserve_order, claim/fill/close, SimulatedBroker), las vistas y las 7 consultas de Grafana. **No probado con TimescaleDB**: Docker Desktop no respondió.
- **Siguiente paso:** aplicar el esquema en la BD de Dokploy (`python /app/scripts/init_db.py` desde la terminal del contenedor). Después, empezar a escribir `account_snapshots` y `risk_events`, y seguir con la fase 2 (`validation.py`, que guarda en `backtest_runs`).

## 2026-10-26 · DESKTOP-PL88VOS

- Se creó la documentación para trabajar en varios dispositivos: `CLAUDE.md`, `docs/DIAGNOSTICO.md`, `docs/PLAN_INTEGRACION_COLAB.md`, esta bitácora y `scripts/diagnostico.py`.
- Se analizó el script de Google Colab (`sistema_trading.py`): validación IS/OOS con permutación y bootstrap, datos Dukascopy, estrategia SMC, informes PDF. Plan de integración en 9 fases.
- `.gitignore`: se añadieron `.coverage`, `.pytest_cache/`, `reports/` y las carpetas que genera el script de Colab.
- Diagnóstico de este equipo: falta `.env`, Docker apagado y `fakeredis` sin instalar. Los tests pasan (50 OK).
- El script completo (1.242 líneas) quedó en `research/colab/sistema_trading.py` y compila. En el plan se añadieron el mapa de secciones, más observaciones y la fase 10 (informe diario por Telegram).
- **Siguiente paso:** fase 2, crear `backtesting/src/validation.py` y aplicarlo a `ma_crossover` y `mean_reversion`.
