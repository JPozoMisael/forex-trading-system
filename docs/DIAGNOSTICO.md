# Diagnóstico técnico del proyecto

> Estado del **código** (no de una computadora en particular). El estado de cada equipo se genera con
> `python scripts/diagnostico.py --guardar` y queda en [diagnosticos/](diagnosticos/).
> Actualizar este archivo cada vez que se cierre o aparezca un pendiente.

**Última revisión:** 2026-11-09 · 173 tests pasan.

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
| P17 | Baja | `init.sql` v3 (`model_evaluations`) no se probó todavía contra un Postgres real | Desde 2026-11-03, `scripts/run_all.py` aplica el esquema al arrancar: en el log del redeploy debe salir "✅ Esquema de la BD al día". Si sale "⚠️ init_db.py terminó con código…", revisar las líneas `init_db \|` del log |
| P16 | **Alta** | El modelo v1 **no está calibrado**: en la prueba final, el % de horas que tocan el objetivo es casi igual en todos los tramos de probabilidad por debajo del 50 % (EUR_USD: 35 % tanto si dijo 27 % como 42 %). Sus probabilidades apenas separan horas buenas de malas, y los tramos de 50 % o más tienen muy pocas horas para concluir nada. En el oro, cualquier compra rindió ~40 % por la subida del oro, no por el modelo (`reports/20261103_0244_calibracion.md`) | Seguir tomando el paper trading como comprobación, no como ventaja. Mejorar la capacidad de separar (otros datos u otro objetivo) con protocolo pre-registrado; `run_calibracion.py` sirve para medir cada versión. Probado 2026-11-03: las herramientas de TradingView (`run_patrones.py`) tampoco separan, y los 4 setups de trader pre-registrados (`run_setups.py`) pierden en el desarrollo en los 3 instrumentos. En H1 con costes reales no se encontró ventaja; falta probar horizontes largos (tendencia de semanas, muchos instrumentos) u otras fuentes de datos |
| P18 | Baja | El sistema opera poco (~2-3 operaciones al mes por instrumento) comparado con traders discrecionales que entran casi a diario (p. ej. compras de oro en M5). Es a propósito: solo entra si en la historia situaciones parecidas terminaron bien ≥ 50 % de las veces, y en M5/M15 los costes se comen la ventaja (oro M15: −4.6 % en la prueba final). Una operación ganadora de otro trader no prueba ventaja: faltan las perdedoras, el riesgo tomado y meses de historial | No bajar el umbral para operar más. Pedir a esos traders su historial completo de 3-6 meses (MT5: Historial → Informe) y analizarlo. Si son rentables y sus reglas se pueden describir, programarlas y validarlas con el mismo protocolo pre-registrado (15 años, costes reales, Bonferroni). Ideas pendientes de decisión para el oro: solo compras con diario alcista, o ruptura en la apertura de Londres/Nueva York |
| P9 | Baja | `fakeredis` está en requirements pero no siempre instalado, así que `test_bus.py` se omite | `pip install -r requirements-local.txt` |

## 3. Riesgos a vigilar

- **Plan decidido el 2026-11-09 (revisión el 2027-02-09)**: el sistema sigue en simulación sin cambios; **no se prueban estrategias nuevas** hasta la revisión (cada prueba sube la exigencia); el usuario opera a mano en su demo de XM (1 % de riesgo, stop siempre, anotar el motivo) y exporta cada mes el informe de historial de MT5 para analizarlo con el mismo rigor. En la revisión se decide con la tabla de calibración en vivo y esas estadísticas. Dinero real solo con evidencia.
- **Calibración en vivo (primeras ~2 semanas, reporte del 2026-11-09)**: va al revés. EUR_USD compra: el tramo 40–50 % tocó el objetivo el 21 % frente al 40 % del tramo < 40 %; venta 40–50 %: 8 %. Oro compra < 40 %: 69 %. Pocas horas y correlacionadas, pero coincide con P16.
- **Sin evidencia suficiente para dinero real**: EUR_USD, USD_JPY y XAU_USD salieron positivos pero no concluyentes en la prueba final. Con 39 combinaciones probadas, el umbral para dinero real es p < 0.00128 (Bonferroni), y la calibración (P16) indica que el modelo casi no separa horas buenas de malas. Mantener `DRY_RUN=true` y comparar el paper trading con `produccion.referencia_backtest` y con el acumulado en vivo del reporte.
- **Cada prueba nueva sube la exigencia**: van 39 combinaciones probadas (4 setups de trader, retroceso al 50 % en oro M5 y seguimiento de tendencia Donchian 55/20 en 8 instrumentos, ninguno aprobado); el umbral para dinero real es p < 0.00128. Pre-registrar en `config.yaml` antes de correr.
- **Tendencia de semanas tampoco**: la cartera de 8 queda en ~0 R antes de costes y pierde después (2026-11-08, `run_tendencia.py`). Con esto, ninguna escala de tiempo probada (M5, H1, H4, diario) da ventaja en estas divisas de 2012 a 2026.
- **Scalping del oro (M5) con stop corto**: probado 2026-11-07 (`run_retroceso50.py`) a partir de las operaciones de un trader en demo. Pierde en los dos periodos (−0.50 R y −0.20 R por operación): con stop de ~2 USD los costes son ~0.31 R por operación y el objetivo se toca el 10 % de las veces. Dos operaciones ganadoras de otro trader no cambian esto sin su historial completo (P18).
- Pendiente menor de la auditoría: en vivo, `MLStrategy` calcula las features dos veces por vela (`probabilities` y `explain`).
- **Hora del servidor MT5**: XM usa GMT+2/+3 y `mt5_client.py` lo convierte a UTC. Cualquier código nuevo que lea velas de MT5 debe pasar por esa conversión.
- **Credenciales**: el `.env` no viaja con git. Si se rota una clave (OANDA, Telegram, Gmail, Postgres de Dokploy), actualizarla en cada equipo y en Dokploy.
