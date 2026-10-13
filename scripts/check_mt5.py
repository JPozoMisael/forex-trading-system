"""
Diagnóstico de la conexión con MetaTrader 5 (XM). No envía ninguna orden.

    python scripts/check_mt5.py

Verifica: conexión, tipo de cuenta (demo/real), Algo Trading habilitado,
nombres de símbolos (sufijo), especificaciones para el cálculo de lotes,
desfase horario del servidor y el riesgo que tendría una operación de ejemplo.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "risk-manager", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shared.config import settings
from shared.mt5_client import MT5Client, mt5, server_utc_offset_seconds
from position_sizing import calculate_lots


def main() -> int:
    ok = True
    client = MT5Client()
    if not client.connect():
        print("[x] No se pudo conectar. Revisa que el terminal MT5 de XM esté abierto y el .env (MT5_LOGIN/PASSWORD/SERVER).")
        return 1

    acc = client.get_account_summary()
    print(f"\n[v] Cuenta {acc.login} @ {acc.server} | {'REAL' if acc.is_real_account else 'DEMO'}")
    print(f"    Balance {acc.balance} {acc.currency} | Equity {acc.equity} | Posiciones abiertas {acc.open_positions_count}")
    if acc.is_real_account and not settings.allow_live_trading:
        print("    [i] Cuenta REAL: el execution-engine NO operará mientras ALLOW_LIVE_TRADING=false (correcto para empezar).")

    if client.terminal_trade_allowed():
        print("[v] Algo Trading habilitado en el terminal")
    else:
        ok = False
        print("[x] Algo Trading DESHABILITADO: pulsa el botón 'Algo Trading' en la barra del terminal MT5")

    offset_h = server_utc_offset_seconds() / 3600
    print(f"[i] Desfase horario del servidor asumido: UTC{offset_h:+.0f} (MT5_SERVER_UTC_OFFSET={settings.mt5_server_utc_offset})")

    print(f"\n[i] Símbolos con sufijo '{settings.mt5_symbol_suffix}':")
    for pair in settings.forex_pairs:
        try:
            spec = client.get_symbol_spec(pair)
        except ValueError as e:
            ok = False
            print(f"  [x] {pair}: {e}")
            continue
        if spec is None:
            ok = False
            print(f"  [x] {pair}: sin información")
            continue
        price = client.get_price(pair)
        sl_pips = 20
        pip = 0.01 if "JPY" in pair else 0.0001
        entry = price["ask"] if price else 0
        lots = calculate_lots(acc.balance, settings.risk_per_trade_pct, entry, entry - sl_pips * pip, spec) if entry else 0
        spread_pips = spec.spread_points * spec.point / pip
        print(
            f"  [v] {pair} -> {spec.symbol} | contrato {spec.contract_size:g} | lote min {spec.volume_min} paso {spec.volume_step} | "
            f"spread {spread_pips:.1f} pips | stops level {spec.stops_level_points} pts\n"
            f"      Con SL de {sl_pips} pips y riesgo {settings.risk_per_trade_pct}% -> {lots} lotes"
        )

        if price:
            age = (datetime.now(timezone.utc) - price["timestamp"]).total_seconds()
            if age > 600:
                print(f"      [i] Último tick hace {age / 60:.0f} min (¿mercado cerrado?)")

    if mt5 is not None and not ok:
        names = [s.name for s in (mt5.symbols_get("*EURUSD*") or [])]
        print(f"\n[i] Símbolos EURUSD disponibles en el servidor: {names} -> ajusta MT5_SYMBOL_SUFFIX")

    client.disconnect()
    print("\n" + ("[v] Todo listo." if ok else "[x] Hay problemas que corregir antes de operar."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
