# Plan: integrar el script de Google Colab (`sistema_trading.py`)

El script de Colab es un sistema "todo en uno": descarga datos, valida, genera las órdenes del día, gráficos y un PDF. Varias de sus piezas son mejores que lo que tiene este repo y vale la pena traerlas, **pero como módulos separados**, no como un script monolítico.

**Fuente:** [research/colab/sistema_trading.py](../research/colab/sistema_trading.py) (1.242 líneas, versión del 2026-10-26). Ese archivo es la referencia; el código integrado vive en `backtesting/` y `services/`.

### Mapa del script

| Líneas | Sección | Destino en el repo |
|---|---|---|
| 1–103 | Configuración (activos, spreads, validación) | `shared/config.py` + `.env.example` |
| 104–262 | Datos: Dukascopy, MT5 en vivo, CSV de MT5, Yahoo | `backtesting/src/data_loader.py` (Dukascopy); MT5 ya existe en `shared/mt5_client.py` |
| 263–368 | Indicadores y `estructura()`: swings, OB, FVG, tendencia | `services/strategy-engine/src/smc.py` (nuevo) |
| 369–733 | Sesgo, liquidez, S/R, objetivos, modos `confirmacion` / `limite` / `fibo`, pivotes, divergencias, confluencias | `strategies/smc_*.py` + `smc.py` |
| 734–830 | Validación: métricas, filtro de exposición, aprobación IS, permutación, bootstrap, veredicto | `backtesting/src/validation.py` |
| 831–950 | Órdenes de hoy: estados `ENTRAR AHORA` / `PENDIENTE` / `VIGILAR`… y lotes | Informe diario (fase 10) |
| 951–1120 | Gráficos estilo TradingView: Fibonacci, pivotes, tendencias, Bollinger, OB/FVG, confluencias, RSI; equity y permutación | `backtesting/src/charts.py` |
| 1121–1242 | Salida en notebook y PDF con `reportlab` | `backtesting/src/report.py` |

## Qué aporta, comparado con el repo

| Pieza del Colab | Qué hace | Lo que hay hoy en el repo | Valor |
|---|---|---|---|
| **Validación** IS/OOS (70/30), aprobación por activo y modo solo con datos IS, test de permutación (operación invertida), intervalo de confianza por bootstrap, veredicto `VENTAJA CON EVIDENCIA` / `SOLO DEMO` | Responde si la estrategia gana por ventaja o por suerte | Métricas descriptivas (`metrics.py`), sin separación de muestra | ⭐⭐⭐ |
| **Costos realistas** (spread por activo + slippage, en R) | Evita backtests optimistas | Un solo `--spread` fijo | ⭐⭐⭐ |
| **Datos Dukascopy** gratis, 15m, 2 años, caché incremental | Historia larga sin depender de MT5 ni de OANDA | MT5, BD, CSV o sintético | ⭐⭐ |
| **Informe**: gráficos (equity, operaciones sobre el precio, Fibonacci y confluencias) + PDF | Informes y gráficas compartibles | Solo tabla en consola | ⭐⭐ |
| **Estrategia SMC**: sesgo multi-timeframe (semana, rango, EMA, estructura 1h/4h, diario, RSI), liquidez (Asia, PDH/PDL, PWH/PWL), OB/FVG, ventana Londres-NY, blackout de noticias | Estrategia nueva y más elaborada | EMA cross y Bollinger+RSI | ⭐⭐ (solo si la validación la aprueba) |
| **Modos de entrada**: `confirmacion` (a mercado), `limite` y `fibo` (órdenes limitadas) | Más formas de entrar | Solo órdenes a mercado | ⭐ (requiere soporte de órdenes pendientes en ejecución) |
| **Filtro de exposición** al USD (máx. 2 por lado) | Evita apostar varias veces a lo mismo | Límite de posiciones y uno por par | ⭐⭐ |
| **Activos** XAUUSD, BTCUSD, USDCAD, USDCHF | Más mercados | 4 pares con USD | ⭐ (depende de P5) |

## Observaciones sobre el script (para corregir al portarlo)

1. **Estado global**: `ACT`, `CACHE_SESGO`, `MT5`, `SPREAD`, etc. se modifican desde todas partes. Al portarlo, cada componente debe recibir sus datos como parámetros, para poder testearlo.
2. **`cargar_mt5` (modo `mt5_csv`)** hace `tz_localize(MT5_TZ)` con `MT5_TZ = "auto"`, que no es una zona horaria válida y va a fallar. Hay que usar la zona resuelta, como hace `zona_servidor()`.
3. **Cuenta demo o real**: el script decide por `'demo' in ACC.server`. El repo usa `trade_mode` en `shared/mt5_client.py`, que es más fiable. Reutilizar ese.
4. **Duplica la conexión MT5**: ya existe en `shared/mt5_client.py` (símbolos con sufijo, hora del servidor). No mantener dos versiones.
5. **Comparaciones múltiples**: se prueban 8 activos × 3 modos = 24 combinaciones en IS. Que el veredicto final se juzgue sobre la cartera OOS está bien, pero conviene informar cuántas combinaciones se probaron y no reajustar parámetros mirando el OOS.
6. **Sin mirar el futuro**: las zonas (`fin`) y los swings (`conf`) usan su hora de confirmación, y está bien hecho. Mantenerlo y añadir tests que lo verifiquen.
7. **Mismo bar SL/TP → SL** (conservador): mantener.
8. **Lote mínimo por encima del riesgo**: cuando el lote calculado es menor que el mínimo, `lotes()` devuelve el mínimo con la nota "(supera el riesgo)". El sistema automático **rechaza** en ese caso (fail-closed), y así debe seguir.
9. **Operar a mano en la misma cuenta**: las "órdenes de hoy" son para ejecutarlas manualmente. Si el execution-engine corre en la misma cuenta MT5, las posiciones manuales no tienen el `MT5_MAGIC` del sistema y no se reconcilian, pero sí consumen margen y equity, y pueden disparar el circuit breaker diario. Usar cuentas demo separadas o integrar todo en el pipeline.
10. **Dependencias que se instalan solas**: el script hace `pip install` al arrancar. En el repo van en `backtesting/requirements.txt` (`reportlab`, `dukascopy-python`).

## Fases (plan combinado, 2026-10-27)

Une las fases originales de este plan con el **MVP del modelo predictivo** (LightGBM). La validación
es la pieza central: cualquier estrategia (EMA, Bollinger, ML o SMC) pasa por el mismo backtester y
la misma validación, y solo la que demuestre ventaja fuera de muestra se activa, primero en demo.

Cada fase se cierra con tests verdes y una entrada en [BITACORA.md](BITACORA.md).

**MVP: primero algo que funcione de punta a punta (EUR_USD H1, OANDA demo)**

1. ✅ **Traer la fuente**: el script completo está en `research/colab/` (2026-10-26).
2. ✅ **Costos y ejecución realistas** en el backtester (2026-10-27, `9ddf2bf`): spread completo, slippage en todas las salidas, comisión, entrada a la apertura siguiente, SL si SL y TP caen en la misma vela, límite de pérdida diaria. *Pendiente:* tabla de spread y slippage por símbolo (va con la fase 7).
3. ✅ **Datos** (2026-10-27): Dukascopy es la fuente única para entrenar, hacer backtest y paper trading, porque OANDA no acepta clientes de Ecuador. Precio medio desde bid/ask, caché en `data/` como parquet, `scripts/descargar_datos.py`. EUR_USD H1 desde 2020-10.
4. ✅ **Validación** (2026-10-27) → `backtesting/src/validation.py`, sirve para cualquier `BaseStrategy`:
   - **Walk-forward** por ventanas, con un hueco entre entrenamiento y prueba igual al horizonte del modelo.
   - Del Colab: **test de permutación**, **intervalo de confianza por bootstrap** y **veredicto** (`VENTAJA CON EVIDENCIA` / `SOLO DEMO`).
   - Siempre contra tres **líneas base**: no operar, buy & hold y `ma_crossover`, todas con costos.
5. ✅ **Modelo predictivo** (2026-10-27; primera versión SIN VENTAJA fuera de muestra, ver bitácora) → `MLStrategy(BaseStrategy)`, igual en el backtester y en el strategy-engine:
   - Features calculadas solo con velas cerradas: retornos, ATR, RSI, distancia a EMAs, volatilidad, hora y día.
   - Target de **triple barrera**: ¿toca primero el TP o el SL (en múltiplos de ATR) dentro de N velas?
   - LightGBM, que solo opera si la probabilidad supera un umbral configurable.
6. **Informe** → `backtesting/src/report.py`: curva de equity (PNG) y tabla de métricas por ventana en `reports/`. El PDF con `reportlab` viene después.
7. **Paper trading** independiente: script con datos de Dukascopy, sin Docker, con órdenes simuladas y log en archivos. `config.yaml` (par, temporalidad, modelo, riesgo, costos) cargado desde `shared/config.py`; claves solo en `.env`. Pipeline en un comando (entrenar → validar → señales) y README.

**Después del MVP**

8. **Generalizar símbolos** (P5): pip y contrato por símbolo para XAUUSD y BTCUSD, en `shared/fx.py`.
9. **Estrategia SMC** como `BaseStrategy` (modo `confirmacion` primero, que es a mercado y encaja con el pipeline actual). Pasa por la misma validación que el modelo; se activa en vivo **solo** si da `VENTAJA CON EVIDENCIA`.
10. **Guardar validaciones en la BD** (`backtest_runs`, `backtest_trades`), además de en `reports/`.
11. **Fuente Dukascopy en el pipeline** (P10): `MARKET_DATA_PROVIDER=dukascopy` en el data-collector, para que el sistema desplegado en Dokploy tenga datos. Para órdenes en una cuenta demo real: XM (MT5), si acepta Ecuador.
12. **Órdenes limitadas** (`limite`, `fibo`): requieren soportar órdenes pendientes en `Broker`, `order_manager` y reconciliación. Es el cambio más grande, así que se deja para el final.
13. **Grafana**: paneles con las métricas de validación y la equity de la cartera aprobada.
14. **Informe diario automático**: a la apertura de Londres, generar el PDF (análisis por activo, órdenes y veredicto) y enviarlo por Telegram desde `monitoring`. Así lo que hoy se hace a mano en Colab queda programado.
