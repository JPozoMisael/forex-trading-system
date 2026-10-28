# Bitácora de trabajo

Registro corto de cada sesión, para retomar desde cualquier computadora. **Lo más reciente arriba.**
Formato: fecha · equipo · qué se hizo · qué quedó pendiente / siguiente paso.

---

## 2026-10-28 (oro, más pares y reportes en hora de Ecuador) · DESKTOP-RC7UDV1

- **Primera operación del modelo en Dokploy:** VENTA de EUR/USD a 1.11265, abierta a las 5:00 a. m. de Ecuador (10:00 UTC), en simulación.
- **Reportes en hora local** (`shared/timefmt.py`, `REPORT_UTC_OFFSET_HOURS=-5`, `REPORT_TIMEZONE_NAME=Ecuador`). El usuario leyó "Abierta (UTC) 10:00" como una hora a la que había que esperar. Ahora todo sale en hora de Ecuador con a. m./p. m.:
  - cada posición explicada en palabras (cuánto se pierde en el SL, cuánto se gana en el TP, cómo va ahora y que no hay que hacer nada);
  - el modelo explicado ("probabilidad de que una COMPRA llegue a su objetivo: 20 %");
  - las sesiones en hora local;
  - `REPORT_TIMES=01:30,08:00` en hora local (reemplaza a `REPORT_TIMES_UTC`).
- **Oro y más pares:** lote de 100 onzas y costes por instrumento en `shared/fx.py`. Protocolo pre-registrado en `config.yaml` (`multi_instrumento`, commit `9daa499`) y corrido con `backtesting/src/run_instrumentos.py`. El strategy-engine carga un modelo por cada instrumento de `produccion.activos`.

## 2026-10-27 (arreglo: reporte repetido cada minuto) · DESKTOP-RC7UDV1

- **Fallo:** el turno enviado se guardaba en Redis como string suelto (`set_json` no lo codifica en JSON) y `get_json` no podía leerlo, así que el reporte salía en cada chequeo (cada minuto). Ahora se guarda `{"turno": ...}`. Un test simula 8 h de chequeos con fakeredis y exige exactamente dos envíos.
- Los reportes salen **antes de cada sesión**: `REPORT_TIMES_UTC=06:30,13:00` (1:30 y 8:00 en Ecuador), de lunes a viernes. Si el servicio estuvo caído a esa hora, solo se recupera dentro de 2 h.
- **Pedido del usuario: más pares y oro (XAU/USD).** Hoy solo hay modelo para EUR_USD H1. Cada instrumento necesita su propio modelo validado con el mismo protocolo. El oro requiere además generalizar pip y contrato (P5).

## 2026-10-27 (avisos de fallo y reporte diario) · DESKTOP-RC7UDV1

- **Avisos de fallo** (`services/monitoring/src/health_check.py`): además de BD, Redis y cuenta, avisa si no llegan velas nuevas (solo con el mercado abierto, dom 22:00 → vie 21:00 UTC) o si el strategy-engine no evalúa la última vela (latido en Redis `state:strategy:<PAR>`). Avisa por email y Telegram solo si el fallo dura 3 chequeos seguidos (un redeploy no genera alertas), y una vez al recuperarse.
- **Reporte diario** (`daily_report.py`), de lunes a viernes (horarios en `REPORT_TIMES_UTC`, ver arreglo de arriba):
  - estado del sistema y probabilidades del modelo en la última vela;
  - cuenta simulada, operaciones de 24 h y acumulado frente al backtest;
  - gráfico con EMA 50/200, Fibonacci del último movimiento, sesiones de Londres y Nueva York, y posición abierta con SL/TP.
  - Si el monitoring estuvo caído a esa hora, lo envía al volver.
  - Prueba manual: `python services/monitoring/src/daily_report.py --enviar` (o `--guardar carpeta`).
- Fibonacci, EMAs y sesiones son contexto visual: el modelo no los usa. 115 tests pasan.
- **Idea anotada:** operar solo en las sesiones de Londres y Nueva York es una variante nueva del modelo. Hay que pre-registrarla y validarla con datos que no se hayan usado (paper trading o una prueba final nueva) antes de activarla.

## 2026-10-27 (paso 3: el modelo en Dokploy) · DESKTOP-RC7UDV1

- El strategy-engine desplegado usa el modelo en lugar de `ma_crossover` y `mean_reversion` (sin ventaja). Se elige con `ACTIVE_STRATEGIES` (por defecto `ml`).
- `ml/serving.py`: carga `models/ml_EUR_USD_H1.joblib` o, en un contenedor nuevo, lo entrena al arrancar con 15 años de Dukascopy (probado: 52 s, las mismas 96.661 muestras que el modelo local). `pipeline.py entrenar` guarda con la misma función.
- Con las 1000 velas que lee de la BD, la probabilidad es **idéntica** a la calculada con toda la historia (diferencia 0 en 200 velas): en vivo decide lo mismo que en el backtest.
- El modelo solo opera su par y temporalidad (`supports()`); avisa en el log si `FOREX_PAIRS`/`CANDLE_GRANULARITY` no coinciden con `config.yaml`.
- Dockerfile: `libgomp1` (LightGBM), `config.yaml` y dependencias del modelo. 100 tests pasan.
- Arreglo `96e67f8`: `_get_list` pasaba `ml` a mayúsculas y el strategy-engine se caía con "Estrategia desconocida 'ML'".
- **Verificado en Dokploy (21:34 UTC):** el modelo se entrenó en ~42 s con las mismas 96.661 muestras (2011-11-07 a 2026-10-23), `Estrategias activas: ['ML_LightGBM']`, data-collector con Dukascopy y monitoring `UP` (PG, Redis y Broker healthy).
- **Siguiente paso:** dejarlo correr semanas con `DRY_RUN=true` (unas 25 señales al año, así que pueden pasar días sin ninguna) y después comparar sus operaciones (tabla `orders`) con lo que da el backtest en las mismas fechas.

## 2026-10-27 (paso 2: Dukascopy en el pipeline desplegado) · DESKTOP-RC7UDV1

- `shared/dukascopy.py` es la única implementación de la descarga: la usan el backtester (`data_loader.py`) y el nuevo `DukascopyClient(MarketDataClient)`.
- `MARKET_DATA_PROVIDER=dukascopy` en el data-collector (P10 resuelto). Sin credenciales. Probado en real: backfill de 1000 velas H1 en ~3 s, solo velas cerradas, precio bid/ask de 1 minuto.
- `.env.production.example` y `.env.example` usan `dukascopy` por defecto. Dependencia `dukascopy-python` en `services/data-collector/requirements.txt`.
- **Pendiente en Dokploy:** `git push` y *Redeploy* (la variable `MARKET_DATA_PROVIDER=dukascopy` ya estaba puesta). El strategy-engine sigue con `ma_crossover` y `mean_reversion`, que no tienen ventaja; con `DRY_RUN=true` solo simula.

## 2026-10-27 (paso 1: mejorar el modelo) · DESKTOP-RC7UDV1

- **Protocolo anti-sobreajuste** (commit `f9a7a9c`, hecho ANTES de correr nada):
  - Historia desde 2011.
  - Corte `inicio_prueba_final = 2023-10-27`: las variantes se comparan solo antes de esa fecha (desarrollo, 18 ventanas de 2014-10 a 2023-10).
  - 4 variantes y la regla de selección quedaron registradas en `config.yaml`.
  - `backtesting/src/run_experiments.py` corre el desarrollo y, con `--final`, la prueba final.
- **Desarrollo:**

  | Variante | Retorno | PF | Ops | Expectativa | Veredicto |
  |---|---|---|---|---|---|
  | v1_base | +8.2 % | 1.02 | 695 | +0.019 R | Positivo no concluyente |
  | v2_extendido | −41.3 % | 0.91 | 830 | −0.057 R | Sin ventaja |
  | v3_extendido_regularizado | +3.6 % | 1.09 | 64 | +0.063 R | Positivo no concluyente |
  | v4_regularizado_umbral_055 | −2.4 % | 0.71 | 12 | −0.193 R | Insuficiente |

  La regla elige **v1_base** (v3 no llega a 100 operaciones). Las features extendidas empeoran el modelo.
- **Prueba final, una vez:** v1 entrenado desde 2011 da **+2.4 %**, DD 9.1 %, PF 1.05, 76 ops, +0.04 R, p = 0.31: **POSITIVO PERO NO CONCLUYENTE**. Gana a no operar (0 %) y a ma_crossover (−36.3 %), **pierde contra buy & hold (+5.3 %)**. Antes, con 6 años de historia, daba −16 %: más historia lo vuelve más selectivo (76 operaciones en vez de 569).
- La prueba final 2023-10 → 2026-10 **ya está usada**. Para la próxima ronda de variantes hace falta datos nuevos: paper trading hacia adelante o una prueba final más reciente.

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
