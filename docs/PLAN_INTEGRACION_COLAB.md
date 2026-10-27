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

## Fases propuestas

Cada fase se cierra con tests verdes y una entrada en [BITACORA.md](BITACORA.md).

1. ✅ **Traer la fuente**: el script completo está en `research/colab/` (2026-10-26).
2. **Validación estadística** → `backtesting/src/validation.py`: corte IS/OOS, permutación, bootstrap, veredicto. Sirve para cualquier estrategia, así que primero se aplica a `ma_crossover` y `mean_reversion` existentes. *Es lo que más sube el nivel del sistema.*
3. **Costos por símbolo** en el backtester (tabla de spread + slippage, convertido a R).
4. **Dukascopy** como `--source dukascopy` en `backtesting/src/data_loader.py`, con caché en `data/` (ya ignorada por git).
5. **Informes** → `backtesting/src/report.py`: gráficos PNG + PDF (`reportlab`) + resumen markdown en `reports/` (sin versionar).
6. **Generalizar símbolos** (P5): pip y contrato por símbolo para XAUUSD y BTCUSD, en `shared/fx.py`.
7. **Estrategia SMC** como `BaseStrategy` (modo `confirmacion` primero, que es a mercado y encaja con el pipeline actual). Activarla en vivo **solo** si la fase 2 da `VENTAJA CON EVIDENCIA`.
8. **Órdenes limitadas** (`limite`, `fibo`): requieren soportar órdenes pendientes en `Broker`, `order_manager` y reconciliación. Es el cambio más grande, así que se deja para el final.
9. **Grafana**: paneles con las métricas de validación y la equity de la cartera aprobada.
10. **Informe diario automático**: a la apertura de Londres, generar el PDF (análisis por activo, órdenes y veredicto) y enviarlo por Telegram desde `monitoring`. Así lo que hoy se hace a mano en Colab queda programado.
