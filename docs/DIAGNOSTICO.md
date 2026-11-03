# Diagnóstico técnico del proyecto

> Estado del **código** (no de una computadora en particular). El estado de cada equipo se genera con
> `python scripts/diagnostico.py --guardar` y queda en [diagnosticos/](diagnosticos/).
> Actualizar este archivo cada vez que se cierre o aparezca un pendiente.

**Última revisión:** 2026-11-03 · 173 tests pasan.

## 1. Qué funciona hoy

| Área | Estado | Dónde |
|---|---|---|
| Datos de mercado OANDA (REST) y MT5 (XM) | ✅ | `services/data-collector/`, `shared/mt5_client.py` |
| Modelo LightGBM (`MLStrategy`), un modelo por instrumento de `produccion.activos` (EUR_USD, USD_JPY, XAU_USD en H1); se entrena al arrancar si falta | ✅ paper trading, sin evidencia para dinero real | `services/strategy-engine/src/ml/` |
| Estrategias `ma_crossover` (EMA 9/21 + EMA 200) y `mean_reversion` (Bollinger + RSI), inactivas por defecto | ✅ validadas: SIN VENTAJA | `services/strategy-engine/src/strategies/` |
| Riesgo fail-closed: lotes con `tick_value`, circuit breaker sobre equity, límite de posiciones, spread, R:R mínimo | ✅ | `services/risk-manager/src/` |
| Ejecución simulada (`SimulatedBroker`) y real MT5 (`MT5Broker`), idempotente y reconciliada | ✅ | `services/execution-engine/src/` |
| Alertas Telegram / email al instante (señal, orden, cierre, bloqueo), health check (BD, Redis, cuenta, velas, modelo), reportes 1:30 y 8:00 en hora de Ecuador con gráficos multi-temporalidad, calendario económico y correo de operación cerrada con gráfico | ✅ | `services/monitoring/src/` (`charts.py`, `daily_report.py`, `trade_report.py`, `econ_calendar.py`) |
| "Lo que ve el modelo" en el reporte: la compra/venta que haría aunque no haya señal (entrada, SL, TP, dinero) y los 3 datos que más pesaron (`TradeModel.explain`) | ✅ | `daily_report.py`, `ml/model.py`, `ml_strategy.py` |
| Repaso de las 8:00: qué habría pasado cada hora con la compra y la venta (objetivo / stop / en curso) y acumulado en vivo por tramo de probabilidad. El strategy-engine guarda cada evaluación en `model_evaluations` (esquema v3) | ✅ código · ⏳ falta `init_db.py` en el servidor (P17) | `review.py`, `signal_publisher.py`, `infra/postgres/init.sql` |
| Herramientas de análisis del modelo: calibración en la prueba final y estudio de patrones con herramientas de TradingView (conjunto de features `tradingview`) | ✅ | `backtesting/src/run_calibracion.py`, `run_patrones.py`, `ml/tradingview.py` |
| Dashboard Grafana | ✅ básico | `infra/grafana/dashboards/trading_overview.json` |
| Despliegue de los 5 servicios en un solo contenedor (Dokploy) | ✅ | `Dockerfile` raíz + `scripts/run_all.py` |
| Backtester con costes por instrumento y validación walk-forward + permutación + bootstrap; prueba final pre-registrada por instrumento | ✅ | `backtesting/src/` (`validation.py`, `run_instrumentos.py`) |

## 2. Problemas conocidos

| # | Prioridad | Problema | Arreglo propuesto |
|---|---|---|---|
| P1 | Baja | No existe ejecución real con OANDA. **OANDA no acepta clientes de Ecuador**, así que deja de ser prioridad | Broker demo alternativo: XM (MT5, ya soportado) si acepta Ecuador |
| P10 | ✅ | El sistema desplegado no tenía fuente de datos utilizable (OANDA no disponible, MT5 solo Windows) | Resuelto 2026-10-27: `MARKET_DATA_PROVIDER=dukascopy` (`shared/dukascopy.py`, `DukascopyClient`) |
| P2 | ✅ | El backtester no tenía validación estadística | Resuelto: `validation.py` (walk-forward + permutación + bootstrap) |
| P3 | Media | `shared/config.py` usa `mt5` como proveedor por defecto, pero `.env.example` y el despliegue usan `oanda` | Alinear el default |
| P4 | Media | Dockerfiles heterogéneos: el raíz (el que usa Dokploy) y `data-collector` usan Python 3.13, el resto 3.12 | Unificar versión o borrar los Dockerfiles por servicio si ya no se usan |
| P5 | Baja | Oro resuelto (lote de 100 oz, costes por instrumento en `shared/fx.py`); siguen sin soporte los cruces sin USD y BTC | Generalizar la conversión a USD si se necesitan |
| P6 | ✅ | `.coverage` estaba versionado | Resuelto 2026-10-26: está en `.gitignore` y se sacó del índice |
| P7 | Baja | `requirements-local.txt` incluye `MetaTrader5`, que no se instala fuera de Windows | Separar en `requirements-mt5.txt` |
| P8 | Baja | No hay CI | Workflow de GitHub Actions con `pytest` |
| P11 | Media | La simulación cierra exactamente en el SL: no modela el deslizamiento en noticias (Fed, PIB, PCE), así que el paper trading es algo optimista | Calendario económico en el reporte; luego evaluar un filtro de noticias con backtest |
| P12 | Media | El `SimulatedBroker` no cobra swap a las posiciones que pasan la noche: el paper trading sale un poco optimista (el backtest de H1 tampoco lo cobraba) | Cobrar `financiamiento_anual_pct` también al cerrar en simulación |
| P13 | Media | En Dokploy `models/` no persiste: cada redeploy reentrena (~5 min sin análisis) y el modelo cambia un poco | Volumen en `/app/models` |
| P14 | Media | Dinero real con XM exige MT5, que solo corre en Windows; el servidor de Dokploy es Linux | Antes de operar real: VPS Windows con MT5 o un broker con API que acepte Ecuador |
| P15 | Baja | Sin reentrenamiento programado: el modelo se queda fijo y **no aprende solo** de lo que pasa en vivo | Reentrenar cada 6 meses (como en la validación walk-forward) y registrar cada versión. Desde v3, `model_evaluations` guarda lo que vio cada hora: sirve para medir cada versión en vivo |
| P17 | Media | La tabla `model_evaluations` (esquema v3) no existe en el servidor hasta correr `init_db.py`; `init.sql` v3 no se probó todavía contra un Postgres real | Tras el redeploy: `python /app/scripts/init_db.py` en el contenedor y comprobar con `--verificar`. Sin ella, el reporte sale igual y avisa que faltan evaluaciones |
| P16 | **Alta** | El modelo v1 **no está calibrado**: en la prueba final, el % de horas que tocan el objetivo es casi igual en todos los tramos de probabilidad por debajo del 50 % (EUR_USD: 35 % tanto si dijo 27 % como 42 %). Sus probabilidades apenas separan horas buenas de malas, y los tramos de 50 % o más tienen muy pocas horas para concluir nada. En el oro, cualquier compra rindió ~40 % por la subida del oro, no por el modelo (`reports/20261103_0244_calibracion.md`) | Seguir tomando el paper trading como comprobación, no como ventaja. Mejorar la capacidad de separar (otros datos u otro objetivo) con protocolo pre-registrado; `run_calibracion.py` sirve para medir cada versión. Probado 2026-11-03: las herramientas de TradingView (`run_patrones.py`) tampoco separan; falta probar otro planteamiento de la operación u otras fuentes de datos |
| P9 | Baja | `fakeredis` está en requirements pero no siempre instalado, así que `test_bus.py` se omite | `pip install -r requirements-local.txt` |

## 3. Riesgos a vigilar

- **Sin evidencia suficiente para dinero real**: EUR_USD, USD_JPY y XAU_USD salieron positivos pero no concluyentes en la prueba final. Con 25 combinaciones probadas, el umbral para dinero real es p < 0.002 (Bonferroni), y la calibración (P16) indica que el modelo casi no separa horas buenas de malas. Mantener `DRY_RUN=true` y comparar el paper trading con `produccion.referencia_backtest` y con el acumulado en vivo del reporte.
- **Cada prueba nueva sube la exigencia**: los 4 setups de trader propuestos (pendientes de aprobación) sumarían 12 combinaciones, 37 en total, y el umbral bajaría a p < 0.00135. Pre-registrar en `config.yaml` antes de correr.
- **Hora del servidor MT5**: XM usa GMT+2/+3 y `mt5_client.py` lo convierte a UTC. Cualquier código nuevo que lea velas de MT5 debe pasar por esa conversión.
- **Credenciales**: el `.env` no viaja con git. Si se rota una clave (OANDA, Telegram, Gmail, Postgres de Dokploy), actualizarla en cada equipo y en Dokploy.
