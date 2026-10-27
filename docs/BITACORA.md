# Bitácora de trabajo

Registro corto de cada sesión, para retomar desde cualquier computadora. **Lo más reciente arriba.**
Formato: fecha · equipo · qué se hizo · qué quedó pendiente / siguiente paso.

---

## 2026-10-27 (cierre) · DESKTOP-RC7UDV1

- **Fase 6, informe** (`backtesting/src/report.py`): `reports/<fecha>_<PAR>_<TF>/` con `informe.md` (empieza respondiendo "¿el modelo supera a las líneas base?"), `equity.png` y las operaciones en CSV.
- **Fase 7, paper trading y pipeline:**
  - `backtesting/src/paper_trader.py` simula órdenes con las mismas reglas del backtester. Un test comprueba que da **las mismas operaciones** que el backtest, también tras reiniciarse.
  - `pipeline.py` hace todo con un comando: `datos`, `validar`, `entrenar`, `senales [--continuo]`. Logs y estado en `logs/paper/`; modelo en `models/`.
  - README con instalación, configuración, uso y el resultado actual. 86 tests pasan.
- Arreglo: `make_windows` aceptaba solo años enteros (lo detectó `tests/test_report.py`).
- **MVP de punta a punta terminado, pero el modelo no tiene ventaja.** Siguientes pasos posibles:
  1. Mejorar el modelo reservando el último año como prueba final intocable.
  2. Fuente Dukascopy en el pipeline desplegado (P10).
  3. Dejar `pipeline.py senales --continuo` corriendo unas semanas para comparar paper trading con backtest.

---

## 2026-10-27 (noche) · DESKTOP-RC7UDV1

- **Fase 4, validación** (`backtesting/src/validation.py`, `run_validation.py`, `config.yaml`): walk-forward con ventana creciente (3 años de entrenamiento y ventanas de prueba de 6 meses; 6 ventanas de 2023-10 a 2026-10), saldo encadenado, líneas base (no operar y buy & hold), permutación contra la operación opuesta, bootstrap y veredicto del Colab.
- **Fase 5, modelo** (`services/strategy-engine/src/ml/`, `strategies/ml_strategy.py`):
  - Features causales y target de triple barrera con SL 2 ATR, TP 3 ATR, horizonte de 48 velas y costos.
  - Dos LightGBM (compras y ventas); umbral de 0.50, fijado antes de ver resultados (punto de equilibrio ≈ 0.45).
  - `MLStrategy` hereda de `BaseStrategy`. Hay tests de que no hay fuga de datos (`tests/test_ml.py`).
- **Resultado fuera de muestra (costos reales), el modelo NO supera a las líneas base:**

  | Estrategia | Retorno | Max DD | Profit factor | Ops | Veredicto |
  |---|---|---|---|---|---|
  | Sin operar | 0 % | 0 % | – | 0 | – |
  | Buy & Hold | +5.3 % | 9.1 % | – | 6 | – |
  | **ML LightGBM** | **−16.0 %** | 30.2 % | 0.96 | 569 | SIN VENTAJA (−0.023 R, IC [−0.12, +0.08], p = 0.079) |
  | ma_crossover | −36.3 % | 47.0 % | 0.86 | 402 | SIN VENTAJA |

  La accuracy del clasificador (~0.62) no supera a la de predecir siempre la clase mayoritaria (0.61–0.68) en 11 de 12 casos. La precisión de las señales casi nunca llega al 44.6 % de equilibrio.
- **Siguiente paso:** fases 6 y 7 (informe con gráfico, paper trading y pipeline en un comando), para tener el flujo completo. Después, mejorar el modelo sin mirar el periodo de prueba (por ejemplo, reservar las últimas ventanas como prueba final intocable).

---

## 2026-10-27 (tarde) · DESKTOP-RC7UDV1

- **OANDA no acepta clientes de Ecuador** (la solicitud de cuenta redirige a FTMO). Se descarta como fuente y como broker.
- **Fase 3 hecha, datos de Dukascopy** (gratis, sin cuenta): `load_from_dukascopy()` en `backtesting/src/data_loader.py`. Calcula el precio medio desde bid/ask (convención del backtester), guarda el spread real por vela, solo velas cerradas y caché incremental en `data/dukascopy_<PAR>_<TF>.parquet`.
  - `python scripts/descargar_datos.py` descarga y revisa la calidad. EUR_USD H1: 37.416 velas (2020-10-27 → hoy) en 26 s. Los únicos huecos son Navidad y Año Nuevo.
  - `run_backtest.py` usa `--source dukascopy --granularity H1` por defecto.
- **Línea base real:** `ma_crossover` en 6 años de EUR_USD H1 con spread 1.0 + slippage 0.3 + comisión $7/lote: **−66.9 %**, profit factor 0.82, 788 operaciones. No tiene ventaja.
- **Dokploy arreglado salvo la fuente de datos:**
  - Se creó el servicio Redis en el proyecto (`REDIS_HOST` = su *Internal Host*, más `REDIS_PASSWORD`). Las variables definitivas están en `.env.production.example`.
  - `init_db.py` aplicó el esquema v2 en `tradingsystem-postgres-jgl1jl/trading` (PostgreSQL 15.19, sin TimescaleDB): 8 tablas y 7 vistas.
  - strategy-engine, risk-manager, monitoring y execution-engine arrancan bien. El data-collector falla con `MARKET_DATA_PROVIDER='dukascopy' no soportado` hasta implementar P10.
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
