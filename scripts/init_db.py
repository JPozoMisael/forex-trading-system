"""
Crea o actualiza el esquema de la base de datos (infra/postgres/init.sql) en la BD
configurada en .env (POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD).

    python scripts/init_db.py              # aplica el esquema y muestra el resultado
    python scripts/init_db.py --verificar  # solo muestra el estado, no cambia nada

El esquema es idempotente: se puede ejecutar las veces que haga falta sin perder datos.
Se aplica en UNA transacción: si algo falla, la BD queda exactamente como estaba.

En Dokploy la BD suele estar en la red interna (p.ej. tradingsystem-postgres-xxxx): ejecútalo
desde la terminal del contenedor de la aplicación, que ya tiene el .env y este script:
    python /app/scripts/init_db.py
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg2

from shared.config import settings

ESQUEMA = os.path.join(ROOT, "infra", "postgres", "init.sql")
TABLAS = ["schema_version", "candles", "signals", "orders",
          "account_snapshots", "risk_events", "backtest_runs", "backtest_trades"]


def mostrar_estado(conn):
    with conn.cursor() as cur:
        cur.execute("SHOW server_version;")
        version = cur.fetchone()[0]
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';")
        ts = cur.fetchone()
        print(f"[i] PostgreSQL {version} | TimescaleDB: {'v' + ts[0] if ts else 'no (tablas normales)'}")

        cur.execute("SELECT to_regclass('schema_version') IS NOT NULL;")
        if cur.fetchone()[0]:
            cur.execute("SELECT version, description, applied_at FROM schema_version ORDER BY version;")
            for v, desc, fecha in cur.fetchall():
                print(f"[v] Esquema v{v} aplicado el {fecha:%Y-%m-%d %H:%M %Z}: {desc}")
        else:
            print("[i] Sin tabla schema_version: la BD está vacía o tiene el esquema v1.")

        print("\nTablas:")
        for t in TABLAS:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (t,))
            if not cur.fetchone()[0]:
                print(f"  [x] {t}: no existe")
                continue
            cur.execute(f'SELECT COUNT(*) FROM "{t}";')
            print(f"  [v] {t}: {cur.fetchone()[0]:,} filas")
        if ts:
            cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1;")
            print(f"  Hypertables: {', '.join(r[0] for r in cur.fetchall()) or 'ninguna'}")

        cur.execute("SELECT table_name FROM information_schema.views WHERE table_schema = 'public' ORDER BY 1;")
        vistas = [r[0] for r in cur.fetchall()]
        print(f"\nVistas: {', '.join(vistas) or 'ninguna'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea o actualiza el esquema de la base de datos")
    parser.add_argument("--verificar", action="store_true", help="solo mostrar el estado, sin cambios")
    args = parser.parse_args()

    destino = f"{settings.postgres_user}@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    try:
        conn = psycopg2.connect(settings.postgres_dsn, connect_timeout=10)
    except Exception as e:
        print(f"[x] No se pudo conectar a {destino}: {str(e).strip()}")
        print("    Revisa POSTGRES_* en .env. Si la BD está en Dokploy, ejecuta este script dentro del contenedor.")
        return 1
    print(f"[v] Conectado a {destino}")

    try:
        if not args.verificar:
            with open(ESQUEMA, encoding="utf-8") as f:
                sql = f.read()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"[x] Error aplicando el esquema (no se cambió nada): {str(e).strip()}")
                return 1
            for aviso in conn.notices:
                print(f"    {aviso.strip()}")
            print("[v] Esquema aplicado.\n")
        mostrar_estado(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
