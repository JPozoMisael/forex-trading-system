"""
Operación de PRUEBA: manda una señal a mano para ver el recorrido completo del
sistema sin esperar a que el modelo llegue al umbral.

    python scripts/operacion_prueba.py                      # compra en GBP_USD
    python scripts/operacion_prueba.py --par EUR_USD --venta

La señal pasa por el risk-manager y el execution-engine como cualquier otra y
manda los mismos correos (señal, orden ejecutada, operación cerrada con gráfico).
Lleva la estrategia "prueba": no cuenta en el balance simulado ni en las
estadísticas del reporte. Solo funciona con DRY_RUN=true.

El stop va a 0.5 ATR (no a 2 ATR como el modelo) para que se cierre en pocas
horas. Por defecto usa GBP_USD, que el modelo no opera: así no bloquea una señal
real del mismo par mientras está abierta.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "strategy-engine", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shared.config import settings
from shared.models import TEST_STRATEGY, Signal, SignalDirection

SL_ATR = 0.5


def build_test_signal(pair: str, buy: bool, close: float, atr: float,
                      now: Optional[datetime] = None) -> Signal:
    """Señal de prueba a mercado: stop a 0.5 ATR y objetivo con el R:R mínimo del risk-manager."""
    if not atr > 0:
        raise ValueError(f"ATR inválido para {pair}: {atr}")
    sign = 1 if buy else -1
    sl_dist = SL_ATR * atr
    tp_dist = max(settings.min_risk_reward_ratio, 1.5) * sl_dist
    return Signal(
        pair=pair,
        strategy_name=TEST_STRATEGY,
        direction=SignalDirection.BUY if buy else SignalDirection.SELL,
        confidence=0.0,
        timestamp=now or datetime.now(timezone.utc),
        entry_price=close,
        stop_loss=round(close - sign * sl_dist, 5),
        take_profit=round(close + sign * tp_dist, 5),
        metadata={"prueba": True, "atr": atr, "sl_atr": SL_ATR},
    )


def _last_close_and_atr(pair: str):
    import pandas as pd
    import psycopg2

    from indicators import calculate_atr

    with psycopg2.connect(settings.postgres_dsn, connect_timeout=5) as conn:
        df = pd.read_sql("SELECT timestamp, high, low, close FROM candles WHERE pair = %s AND granularity = %s "
                         "AND complete ORDER BY timestamp DESC LIMIT 100", conn,
                         params=(pair, settings.candle_granularity))
    if len(df) < 20:
        sys.exit(f"[x] Hay {len(df)} velas de {pair} {settings.candle_granularity} en la BD; hacen falta 20.")
    df = df.iloc[::-1].astype({"high": float, "low": float, "close": float})
    atr = float(calculate_atr(df["high"], df["low"], df["close"], 14).iloc[-1])
    return float(df["close"].iloc[-1]), atr


def main():
    parser = argparse.ArgumentParser(description="Manda una operación de prueba (solo simulación)")
    parser.add_argument("--par", default="GBP_USD")
    parser.add_argument("--venta", action="store_true", help="vender en vez de comprar")
    args = parser.parse_args()
    pair = args.par.upper()

    if not settings.dry_run:
        sys.exit("[x] DRY_RUN no está activo: la operación de prueba solo se permite en simulación.")
    if pair not in settings.forex_pairs:
        sys.exit(f"[x] {pair} no está en FOREX_PAIRS ({', '.join(settings.forex_pairs)}).")

    from signal_publisher import SignalPublisher

    close, atr = _last_close_and_atr(pair)
    sig = build_test_signal(pair, not args.venta, close, atr)
    if not SignalPublisher().publish(sig):
        sys.exit("[x] No se pudo publicar la señal en Redis.")

    print(f"[v] Señal de PRUEBA enviada: {pair} {sig.direction.value.upper()} @ {close} "
          f"| SL {sig.stop_loss} | TP {sig.take_profit}")
    print("    En unos segundos deberían llegar los correos de señal y de orden ejecutada.")
    print("    El de operación cerrada llega cuando una vela toque el SL o el TP (suele ser en pocas horas).")
    print("    Si llega '[Alerta de Riesgo]', el correo dice el motivo del rechazo.")


if __name__ == "__main__":
    main()
