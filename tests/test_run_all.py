"""
El arranque en un solo contenedor (Dokploy) aplica el esquema de la BD antes de
levantar los servicios, y si falla arranca igual.
"""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_all():
    spec = importlib.util.spec_from_file_location("run_all", ROOT / "scripts" / "run_all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_applies_init_db_before_starting(monkeypatch):
    run_all = _run_all()
    calls = []
    monkeypatch.setattr(run_all.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, "[v] Esquema aplicado.", ""))
    assert run_all._apply_schema() is True
    assert calls[0][-1].endswith("init_db.py")


def test_schema_failure_does_not_stop_the_system(monkeypatch):
    run_all = _run_all()
    monkeypatch.setattr(run_all.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "[x] No se pudo conectar", ""))
    assert run_all._apply_schema() is False

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 120)
    monkeypatch.setattr(run_all.subprocess, "run", boom)
    assert run_all._apply_schema() is False
