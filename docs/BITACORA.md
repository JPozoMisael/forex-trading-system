# Bitácora de trabajo

Registro corto de cada sesión, para retomar desde cualquier computadora. **Lo más reciente arriba.**
Formato: fecha · equipo · qué se hizo · qué quedó pendiente / siguiente paso.

---

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
