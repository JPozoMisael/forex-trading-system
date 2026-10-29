# Diagnóstico técnico del proyecto

> Estado del **código** (no de una computadora en particular). El estado de cada equipo se genera con
> `python scripts/diagnostico.py --guardar` y queda en [diagnosticos/](diagnosticos/).
> Actualizar este archivo cada vez que se cierre o aparezca un pendiente.

**Última revisión:** 2026-10-29 · commit `f4f1ca7` · 135 tests pasan.

## 1. Qué funciona hoy

| Área | Estado | Dónde |
|---|---|---|
| Datos de mercado OANDA (REST) y MT5 (XM) | ✅ | `services/data-collector/`, `shared/mt5_client.py` |
| Modelo LightGBM (`MLStrategy`), un modelo por instrumento de `produccion.activos` (EUR_USD, USD_JPY, XAU_USD en H1); se entrena al arrancar si falta | ✅ paper trading, sin evidencia para dinero real | `services/strategy-engine/src/ml/` |
| Estrategias `ma_crossover` (EMA 9/21 + EMA 200) y `mean_reversion` (Bollinger + RSI), inactivas por defecto | ✅ validadas: SIN VENTAJA | `services/strategy-engine/src/strategies/` |
| Riesgo fail-closed: lotes con `tick_value`, circuit breaker sobre equity, límite de posiciones, spread, R:R mínimo | ✅ | `services/risk-manager/src/` |
| Ejecución simulada (`SimulatedBroker`) y real MT5 (`MT5Broker`), idempotente y reconciliada | ✅ | `services/execution-engine/src/` |
| Alertas Telegram / email, health check (BD, Redis, cuenta, velas, modelo), reportes 1:30 y 8:00 en hora de Ecuador con gráficos multi-temporalidad y correo de operación cerrada con gráfico | ✅ | `services/monitoring/src/` (`charts.py`, `daily_report.py`, `trade_report.py`) |
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
| P9 | Baja | `fakeredis` está en requirements pero no siempre instalado, así que `test_bus.py` se omite | `pip install -r requirements-local.txt` |

## 3. Riesgos a vigilar

- **Sin evidencia suficiente para dinero real**: EUR_USD, USD_JPY y XAU_USD salieron positivos pero no concluyentes en la prueba final (ninguno con p < 0.0033). Mantener `DRY_RUN=true` y comparar el paper trading con `produccion.referencia_backtest` durante meses.
- **Hora del servidor MT5**: XM usa GMT+2/+3 y `mt5_client.py` lo convierte a UTC. Cualquier código nuevo que lea velas de MT5 debe pasar por esa conversión.
- **Credenciales**: el `.env` no viaja con git. Si se rota una clave (OANDA, Telegram, Gmail, Postgres de Dokploy), actualizarla en cada equipo y en Dokploy.
