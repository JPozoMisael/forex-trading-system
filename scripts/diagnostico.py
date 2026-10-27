"""
Diagnóstico del entorno de trabajo en ESTA computadora. No envía órdenes ni
muestra credenciales (solo dice si cada variable del .env está completa o vacía).

    python scripts/diagnostico.py              # muestra el informe
    python scripts/diagnostico.py --guardar    # además lo guarda en docs/diagnosticos/
    python scripts/diagnostico.py --sin-tests  # omite pytest (más rápido)

Revisa: Python y dependencias, git (rama, cambios sin subir), .env, Docker,
TimescaleDB, Redis, MetaTrader 5 y la suite de tests. Al cambiar de
computadora, correrlo primero y guardar el informe para que quede en git.
"""
import argparse
import importlib.metadata
import os
import platform
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Variables del .env que deben tener valor según el proveedor
REQUERIDAS = {
    "comun": ["MARKET_DATA_PROVIDER", "FOREX_PAIRS", "CANDLE_GRANULARITY", "DRY_RUN", "POSTGRES_HOST", "REDIS_HOST"],
    "oanda": ["OANDA_API_KEY", "OANDA_ACCOUNT_ID"],
    "mt5": ["MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"],
    "alertas": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "EMAIL_SENDER", "EMAIL_APP_PASSWORD"],
}


class Informe:
    def __init__(self):
        self.lineas = []
        self.problemas = []

    def titulo(self, texto):
        self.lineas.append(f"\n## {texto}\n")

    def ok(self, texto):
        self.lineas.append(f"- [v] {texto}")

    def info(self, texto):
        self.lineas.append(f"- [i] {texto}")

    def mal(self, texto):
        self.lineas.append(f"- [x] {texto}")
        self.problemas.append(texto)

    def texto(self):
        return "\n".join(self.lineas)


def _cmd(args, timeout=30):
    try:
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def _leer_env():
    ruta = os.path.join(ROOT, ".env")
    if not os.path.exists(ruta):
        return None
    valores = {}
    with open(ruta, encoding="utf-8", errors="replace") as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                valores[k.strip()] = v.strip()
    return valores


def _puerto_abierto(host, puerto):
    try:
        with socket.create_connection((host, puerto), timeout=2):
            return True
    except OSError:
        return False


def revisar_sistema(inf):
    inf.titulo("Sistema")
    inf.info(f"Equipo: {socket.gethostname()} | {platform.system()} {platform.release()} | Python {platform.python_version()}")
    if sys.version_info < (3, 12):
        inf.mal(f"Python {platform.python_version()}: el proyecto requiere 3.12+")
    en_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    (inf.ok if en_venv else inf.info)(f"Entorno virtual: {'activo' if en_venv else 'NO (usa .venv)'} ({sys.executable})")


def revisar_dependencias(inf):
    inf.titulo("Dependencias (requirements-local.txt)")
    faltan = []
    with open(os.path.join(ROOT, "requirements-local.txt"), encoding="utf-8") as f:
        for linea in f:
            linea = linea.split("#")[0].strip()
            if not linea:
                continue
            nombre = re.split(r"[<>=!~]", linea)[0].strip()
            try:
                version = importlib.metadata.version(nombre)
                inf.ok(f"{nombre} {version}")
            except importlib.metadata.PackageNotFoundError:
                if nombre == "MetaTrader5" and platform.system() != "Windows":
                    inf.info("MetaTrader5 no instalado (normal fuera de Windows)")
                else:
                    faltan.append(nombre)
    if faltan:
        inf.mal(f"Faltan paquetes: {', '.join(faltan)} -> pip install -r requirements-local.txt")


def revisar_git(inf):
    inf.titulo("Git")
    code, rama, _ = _cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        inf.mal("No es un repositorio git o git no está instalado")
        return
    _, commit, _ = _cmd(["git", "log", "-1", "--format=%h %s (%cr)"])
    inf.info(f"Rama {rama} | último commit: {commit}")
    _, cambios, _ = _cmd(["git", "status", "--short"])
    if cambios:
        inf.info(f"Cambios sin commitear ({len(cambios.splitlines())}):")
        inf.lineas.extend(f"    {c}" for c in cambios.splitlines()[:20])
    else:
        inf.ok("Árbol de trabajo limpio")
    _cmd(["git", "fetch", "--quiet"], timeout=20)
    code, cuenta, _ = _cmd(["git", "rev-list", "--left-right", "--count", f"{rama}...origin/{rama}"])
    if code == 0 and cuenta:
        adelante, atras = cuenta.split()
        if int(atras):
            inf.mal(f"{atras} commit(s) en GitHub que esta computadora no tiene -> git pull")
        if int(adelante):
            inf.info(f"{adelante} commit(s) locales sin subir -> git push")
        if not int(atras) and not int(adelante):
            inf.ok("Sincronizado con origin")
    else:
        inf.info("No se pudo comparar con origin (¿sin conexión?)")


def revisar_env(inf):
    inf.titulo(".env (solo se indica si cada variable tiene valor)")
    env = _leer_env()
    if env is None:
        inf.mal("No existe .env -> copia .env.example a .env y complétalo")
        return {}
    proveedor = env.get("MARKET_DATA_PROVIDER", "mt5").lower()
    inf.info(f"MARKET_DATA_PROVIDER={proveedor} | DRY_RUN={env.get('DRY_RUN', '(vacío: true)')} | "
             f"ALLOW_LIVE_TRADING={env.get('ALLOW_LIVE_TRADING', 'false')}")
    for grupo in ("comun", proveedor, "alertas"):
        for var in REQUERIDAS.get(grupo, []):
            if env.get(var):
                inf.ok(f"{var}: completa")
            elif grupo == "alertas":
                inf.info(f"{var}: vacía (alertas desactivadas)")
            else:
                inf.mal(f"{var}: vacía")
    if env.get("ALLOW_LIVE_TRADING", "false").lower() == "true":
        inf.info("ALLOW_LIVE_TRADING=true: con cuenta real el sistema OPERARÁ dinero real")
    return env


def revisar_docker(inf):
    inf.titulo("Docker")
    code, salida, _ = _cmd(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"], timeout=15)
    if code != 0:
        inf.mal("Docker no responde (¿Docker Desktop cerrado o no instalado?)")
        return
    contenedores = [l for l in salida.splitlines() if l.startswith("forex_")]
    if contenedores:
        for c in contenedores:
            inf.ok(c.replace("\t", " -> "))
    else:
        inf.info("Sin contenedores forex_* corriendo -> docker compose -f docker-compose-dev.yml up -d")


def revisar_postgres(inf):
    inf.titulo("TimescaleDB")
    from shared.config import settings
    if not _puerto_abierto(settings.postgres_host, settings.postgres_port):
        inf.mal(f"No hay nada escuchando en {settings.postgres_host}:{settings.postgres_port}")
        return
    try:
        import psycopg2
        conn = psycopg2.connect(settings.postgres_dsn, connect_timeout=5)
    except Exception as e:
        inf.mal(f"No se pudo conectar: {str(e).splitlines()[0]}")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            tablas = {r[0] for r in cur.fetchall()}
            for t in ("candles", "signals", "orders"):
                (inf.ok if t in tablas else inf.mal)(f"tabla {t}: {'existe' if t in tablas else 'NO existe (correr init.sql)'}")
            if "candles" in tablas:
                cur.execute("SELECT pair, granularity, COUNT(*), MAX(timestamp) FROM candles GROUP BY 1, 2 ORDER BY 1, 2")
                for par, gran, n, ultima in cur.fetchall():
                    inf.info(f"velas {par} {gran}: {n:,} (última {ultima:%Y-%m-%d %H:%M} UTC)")
            if "orders" in tablas:
                cur.execute("SELECT status, COUNT(*) FROM orders GROUP BY 1 ORDER BY 1")
                estados = ", ".join(f"{s}={n}" for s, n in cur.fetchall()) or "ninguna"
                inf.info(f"órdenes por estado: {estados}")
    except Exception as e:
        inf.mal(f"Error consultando la BD: {str(e).splitlines()[0]}")
    finally:
        conn.close()


def revisar_redis(inf):
    inf.titulo("Redis")
    from shared.config import settings
    try:
        import redis
        r = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db,
                        password=settings.redis_password or None, socket_timeout=3)
        r.ping()
    except Exception as e:
        inf.mal(f"No responde en {settings.redis_host}:{settings.redis_port} ({e})")
        return
    inf.ok("Responde")
    streams = [settings.channel_signals_new, settings.channel_orders_approved, settings.channel_orders_executed,
               settings.channel_orders_closed, settings.channel_risk_alerts]
    streams += [f"{settings.channel_market_candle}:{p}" for p in settings.forex_pairs]
    for s in streams:
        if r.exists(s):
            inf.info(f"stream {s}: {r.xlen(s)} mensajes")
    cuenta = r.get(settings.key_account_snapshot)
    (inf.ok if cuenta else inf.info)(
        "snapshot de cuenta publicado por el execution-engine" if cuenta
        else "sin snapshot de cuenta (execution-engine apagado: el risk-manager rechazará todo)")


def revisar_mt5(inf, env):
    inf.titulo("MetaTrader 5")
    if platform.system() != "Windows":
        inf.info("No aplica fuera de Windows")
        return
    try:
        import MetaTrader5 as mt5
    except ImportError:
        inf.info("Librería MetaTrader5 no instalada")
        return
    if (env or {}).get("MARKET_DATA_PROVIDER", "mt5").lower() != "mt5":
        inf.info("MARKET_DATA_PROVIDER no es mt5: se omite la conexión (usa scripts/check_mt5.py si la necesitas)")
        return
    if mt5.initialize():
        info = mt5.terminal_info()
        inf.ok(f"Terminal conectado | Algo Trading {'activo' if info and info.trade_allowed else 'DESACTIVADO'}")
        mt5.shutdown()
    else:
        inf.mal(f"No se pudo conectar al terminal ({mt5.last_error()}). Detalle: python scripts/check_mt5.py")


def revisar_tests(inf):
    inf.titulo("Tests (pytest)")
    code, salida, err = _cmd([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], timeout=600)
    resumen = (salida or err).splitlines()[-1] if (salida or err) else "sin salida"
    (inf.ok if code == 0 else inf.mal)(resumen)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico del entorno del sistema de trading")
    parser.add_argument("--guardar", action="store_true", help="guardar el informe en docs/diagnosticos/")
    parser.add_argument("--sin-tests", action="store_true", help="no correr pytest")
    args = parser.parse_args()

    inf = Informe()
    ahora = datetime.now(timezone.utc).astimezone()
    revisar_sistema(inf)
    revisar_dependencias(inf)
    revisar_git(inf)
    env = revisar_env(inf)
    revisar_docker(inf)
    revisar_postgres(inf)
    revisar_redis(inf)
    revisar_mt5(inf, env)
    if not args.sin_tests:
        revisar_tests(inf)

    cabecera = f"# Diagnóstico de entorno - {socket.gethostname()} - {ahora:%Y-%m-%d %H:%M %z}\n"
    resumen = ("\n## Resumen\n\n" + ("Todo en orden." if not inf.problemas
               else "Problemas encontrados:\n" + "\n".join(f"1. {p}" for p in inf.problemas)))
    informe = cabecera + inf.texto() + "\n" + resumen + "\n"
    print(informe)

    if args.guardar:
        carpeta = os.path.join(ROOT, "docs", "diagnosticos")
        os.makedirs(carpeta, exist_ok=True)
        equipo = re.sub(r"[^A-Za-z0-9_-]", "_", socket.gethostname())
        ruta = os.path.join(carpeta, f"{ahora:%Y-%m-%d}_{equipo}.md")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(informe)
        print(f"[v] Informe guardado en {os.path.relpath(ruta, ROOT)}")
    return 0 if not inf.problemas else 1


if __name__ == "__main__":
    sys.exit(main())
