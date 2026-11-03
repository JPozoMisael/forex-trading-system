"""
Arranca todos los servicios del sistema en un solo contenedor y los vigila.

Pensado para desplegar como UNA aplicación (p.ej. en Dokploy con el
Dockerfile raíz). Cada servicio corre como un proceso aparte: si uno se cae,
se reinicia solo (con espera creciente) sin afectar a los demás. Al recibir
SIGTERM (redeploy/stop) detiene todos de forma ordenada.

    python scripts/run_all.py
    SERVICES=data-collector,strategy-engine python scripts/run_all.py   # solo algunos
"""
import os
import signal
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# El execution-engine va primero: publica el estado de cuenta que necesita el risk-manager.
DEFAULT_SERVICES = ["execution-engine", "monitoring", "risk-manager", "strategy-engine", "data-collector"]

running = True


def _log(msg: str):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | SUPERVISOR | {msg}", flush=True)


def _pipe_output(name: str, proc: subprocess.Popen):
    """Reenvía la salida del servicio con su nombre como prefijo."""
    for line in proc.stdout:
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()


def _start(name: str) -> subprocess.Popen:
    script = os.path.join(ROOT, "services", name, "src", "main.py")
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-u", script],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    threading.Thread(target=_pipe_output, args=(name, proc), daemon=True).start()
    _log(f"▶️ {name} iniciado (pid {proc.pid})")
    return proc


def _apply_schema(timeout: int = 120) -> bool:
    """
    Aplica infra/postgres/init.sql antes de arrancar (scripts/init_db.py): es
    idempotente y va en una sola transacción, así que un redeploy con tablas
    nuevas no necesita ningún paso a mano. Si falla, se avisa y se arranca
    igual (el risk-manager ya rechaza todo si la BD no responde).
    """
    try:
        res = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "init_db.py")], cwd=ROOT,
                             env=dict(os.environ, PYTHONPATH=ROOT), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as e:
        _log(f"⚠️ No se pudo aplicar el esquema de la BD ({e}); se arranca igual")
        return False
    for line in (res.stdout + res.stderr).strip().splitlines()[-8:]:
        _log(f"   init_db | {line}")
    if res.returncode != 0:
        _log(f"⚠️ init_db.py terminó con código {res.returncode}; se arranca igual")
        return False
    _log("✅ Esquema de la BD al día")
    return True


def _handle_shutdown(signum, frame):
    global running
    running = False


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    raw = os.getenv("SERVICES", "").strip()
    services = [s.strip() for s in raw.split(",") if s.strip()] if raw else DEFAULT_SERVICES
    unknown = [s for s in services if not os.path.exists(os.path.join(ROOT, "services", s, "src", "main.py"))]
    if unknown:
        _log(f"❌ Servicios desconocidos: {unknown}")
        sys.exit(1)

    _apply_schema()

    procs = {}
    restarts = {s: 0 for s in services}
    next_start = {s: 0.0 for s in services}

    while running:
        now = time.monotonic()
        for name in services:
            proc = procs.get(name)
            if proc is not None and proc.poll() is None:
                continue  # sigue vivo
            if proc is not None:
                code = proc.returncode
                restarts[name] += 1
                delay = min(60, 2 ** min(restarts[name], 6))
                _log(f"⚠️ {name} terminó con código {code}; reinicio #{restarts[name]} en {delay}s")
                next_start[name] = now + delay
                procs[name] = None
            if now >= next_start[name]:
                procs[name] = _start(name)
                if restarts[name] == 0:
                    time.sleep(2)  # escalona el primer arranque
        time.sleep(1)

    _log("Deteniendo servicios...")
    for name, proc in procs.items():
        if proc is not None and proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + 20
    for name, proc in procs.items():
        if proc is None:
            continue
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
    _log("Todos los servicios detenidos.")


if __name__ == "__main__":
    main()
