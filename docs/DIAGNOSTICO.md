# Diagnóstico técnico del proyecto

> Estado del **código** (no de una computadora en particular). El estado de cada equipo se genera con
> `python scripts/diagnostico.py --guardar` y queda en [diagnosticos/](diagnosticos/).
> Actualizar este archivo cada vez que se cierre o aparezca un pendiente.

**Última revisión:** 2026-10-27 · commit `9ddf2bf` · 62 tests pasan.

## 1. Qué funciona hoy

| Área | Estado | Dónde |
|---|---|---|
| Datos de mercado OANDA (REST) y MT5 (XM) | ✅ | `services/data-collector/`, `shared/mt5_client.py` |
| Estrategias `ma_crossover` (EMA 9/21 + EMA 200) y `mean_reversion` (Bollinger + RSI) | ✅ | `services/strategy-engine/src/strategies/` |
| Riesgo fail-closed: lotes con `tick_value`, circuit breaker sobre equity, límite de posiciones, spread, R:R mínimo | ✅ | `services/risk-manager/src/` |
| Ejecución simulada (`SimulatedBroker`) y real MT5 (`MT5Broker`), idempotente y reconciliada | ✅ | `services/execution-engine/src/` |
| Alertas Telegram / email, health check | ✅ | `services/monitoring/src/` |
| Dashboard Grafana | ✅ básico | `infra/grafana/dashboards/trading_overview.json` |
| Despliegue de los 5 servicios en un solo contenedor (Dokploy) | ✅ | `Dockerfile` raíz + `scripts/run_all.py` |
| Backtester (MT5 / BD / CSV / sintético) con spread completo, slippage, comisión, entrada a la apertura siguiente y límite de pérdida diaria | ⚠️ sin validación estadística (P2) | `backtesting/src/` |

## 2. Problemas conocidos

| # | Prioridad | Problema | Arreglo propuesto |
|---|---|---|---|
| P1 | Alta | No existe ejecución real con OANDA: con `MARKET_DATA_PROVIDER=oanda` solo funciona `DRY_RUN=true` | Implementar `OandaBroker(Broker)` en `brokers.py` |
| P2 | Alta | El backtester no tiene validación estadística: no separa in-sample y out-of-sample ni tiene test de permutación, y no hay forma de saber si una estrategia tiene ventaja real | `validation.py`: walk-forward + permutación + bootstrap del Colab (fase 4 de [PLAN_INTEGRACION_COLAB.md](PLAN_INTEGRACION_COLAB.md)) |
| P3 | Media | `shared/config.py` usa `mt5` como proveedor por defecto, pero `.env.example` y el despliegue usan `oanda` | Alinear el default |
| P4 | Media | Dockerfiles heterogéneos: el raíz (el que usa Dokploy) y `data-collector` usan Python 3.13, el resto 3.12 | Unificar versión o borrar los Dockerfiles por servicio si ya no se usan |
| P5 | Media | El backtester solo acepta pares con USD; no soporta cruces, oro (XAUUSD) ni BTC | Generalizar `shared/fx.py` (pip y contrato por símbolo) |
| P6 | ✅ | `.coverage` estaba versionado | Resuelto 2026-10-26: está en `.gitignore` y se sacó del índice |
| P7 | Baja | `requirements-local.txt` incluye `MetaTrader5`, que no se instala fuera de Windows | Separar en `requirements-mt5.txt` |
| P8 | Baja | No hay CI | Workflow de GitHub Actions con `pytest` |
| P9 | Baja | `fakeredis` está en requirements pero no siempre instalado, así que `test_bus.py` se omite | `pip install -r requirements-local.txt` |

## 3. Riesgos a vigilar

- **Sin evidencia de ventaja**: las dos estrategias del sistema nunca se validaron fuera de muestra con costos. Mientras no lo estén, mantener `DRY_RUN=true` o una cuenta demo.
- **Hora del servidor MT5**: XM usa GMT+2/+3 y `mt5_client.py` lo convierte a UTC. Cualquier código nuevo que lea velas de MT5 debe pasar por esa conversión.
- **Credenciales**: el `.env` no viaja con git. Si se rota una clave (OANDA, Telegram, Gmail, Postgres de Dokploy), actualizarla en cada equipo y en Dokploy.
