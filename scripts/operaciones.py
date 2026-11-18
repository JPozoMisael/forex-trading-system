"""
Últimas operaciones de la base de datos, con la confianza con la que se abrió
cada una (solo lectura: no cambia nada).

    python scripts/operaciones.py              # las últimas 20
    python scripts/operaciones.py --n 50

En Dokploy: terminal del contenedor de la aplicación (usa la BD de las variables
de entorno, no pide contraseña).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shared.config import settings

QUERY = """
SELECT id,
       to_char(opened_at AT TIME ZONE %(tz)s, 'DD/MM HH24:MI') AS abierta,
       pair, strategy_name, direction,
       round((metadata->'signal'->>'confidence')::numeric * 100) AS confianza,
       volume_lots, entry_price, exit_price, exit_reason,
       round(pnl::numeric, 2) AS pnl, round(r_multiple::numeric, 2) AS r, status
FROM orders
ORDER BY id DESC
LIMIT %(n)s
"""
SIDE = {"buy": "COMPRA", "sell": "VENTA"}
TYPE = {"practica": "práctica", "prueba": "prueba"}


def fmt_row(r) -> str:
    (oid, abierta, pair, strategy, direction, conf, lots, entry, exit_p, reason, pnl, rr, status) = r
    tipo = TYPE.get(strategy, "modelo")
    conf_txt = f"{conf:.0f} %" if conf is not None else "-"
    resultado = (f"{'+' if pnl >= 0 else ''}{pnl} USD ({rr:+} R, {reason})" if pnl is not None
                 else ("abierta" if status == "filled" else status))
    return (f"#{oid:<4} {abierta or '-':<11} {pair:<8} {tipo:<8} {SIDE.get(direction, direction):<7} "
            f"conf {conf_txt:<5} lote {lots or 0:<5} entrada {entry or '-':<10} → {resultado}")


def main():
    import psycopg2

    parser = argparse.ArgumentParser(description="Últimas operaciones (solo lectura)")
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()
    with psycopg2.connect(settings.postgres_dsn, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(QUERY, {"tz": "America/Guayaquil", "n": args.n})
        rows = cur.fetchall()
    if not rows:
        print("Todavía no hay operaciones en la base de datos.")
        return
    print(f"Últimas {len(rows)} operaciones (hora de Ecuador):")
    for r in rows:
        print(fmt_row(r))


if __name__ == "__main__":
    main()
