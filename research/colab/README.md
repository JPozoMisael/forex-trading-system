# Investigación en Google Colab

Aquí va el código de referencia que se desarrolla en Colab antes de integrarlo al sistema.

- `sistema_trading.py`: script "todo en uno" (Dukascopy, validación IS/OOS, estrategia SMC, órdenes del día, gráficos, PDF). Versión del 2026-10-26.
  Para correrlo desde aquí: `pip install reportlab dukascopy-python` y `python research/colab/sistema_trading.py`.

Este código **no** lo importa ningún servicio. Lo que se integra se reescribe como módulos en `backtesting/` o `services/` (ver [docs/PLAN_INTEGRACION_COLAB.md](../../docs/PLAN_INTEGRACION_COLAB.md)).
Las carpetas que genera el script (`sistema/`, `datos_bt/`, `mt5/`) están en `.gitignore`.
