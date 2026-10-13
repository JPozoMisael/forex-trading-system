"""
Diagnóstico de la fuente de datos de mercado configurada (MARKET_DATA_PROVIDER).
No envía órdenes. Sirve para cualquier proveedor: oanda, mt5, ...

    python scripts/check_feed.py
"""
import importlib.util
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTOR_SRC = os.path.join(ROOT, "services", "data-collector", "src")
sys.path.insert(0, ROOT)
sys.path.insert(0, COLLECTOR_SRC)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shared.config import settings
from shared.fx import get_pip_size


def _load_collector():
    spec = importlib.util.spec_from_file_location("collector_main", os.path.join(COLLECTOR_SRC, "main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    print(f"[i] Proveedor: {settings.market_data_provider} | Pares: {settings.forex_pairs} | Temporalidad: {settings.candle_granularity}")
    try:
        client = _load_collector().build_market_client()
    except Exception as e:
        print(f"[x] {e}")
        return 1

    ok = True
    now = datetime.now(timezone.utc)
    for pair in settings.forex_pairs:
        try:
            candles = client.get_latest_candles(pair=pair, granularity=settings.candle_granularity, count=3)
        except Exception as e:
            ok = False
            print(f"  [x] {pair}: {e}")
            continue
        if not candles:
            ok = False
            print(f"  [x] {pair}: sin velas")
            continue
        last = candles[-1]
        age_min = (now - last.timestamp).total_seconds() / 60
        price = client.get_price(pair)
        spread = f"{price['spread'] / get_pip_size(pair):.1f} pips" if price else "n/d"
        print(
            f"  [v] {pair}: última vela cerrada {last.timestamp:%Y-%m-%d %H:%M} UTC (hace {age_min:.0f} min) "
            f"close={last.close} | spread actual {spread}"
        )
        if age_min > 60:
            print("      [i] La última vela es vieja: ¿mercado cerrado (fin de semana)?")

    if hasattr(client, "disconnect"):
        client.disconnect()
    print("\n" + ("[v] Fuente de datos OK." if ok else "[x] Hay problemas con la fuente de datos."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
