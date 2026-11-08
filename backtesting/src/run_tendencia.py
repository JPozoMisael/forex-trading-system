"""
Prueba ÚNICA del seguimiento de tendencia (Donchian 55/20) PRE-REGISTRADO en
config.yaml (sección tendencia, 2026-11-07), como UNA cartera de 8 instrumentos.

    python backtesting/src/run_tendencia.py

Escribe reports/<fecha>_tendencia/: informe.md, operaciones.csv y la curva de
resultados acumulados (cartera_r.png).
"""
import os
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(ROOT, "services/strategy-engine/src"))
sys.path.insert(0, os.path.join(ROOT, "services/risk-manager/src"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from run_instrumentos import PAPER_VERDICTS
from shared.config import load_trading_config
from tendencia import Params, simulate
from validation import make_verdict

RISK_PCT = 1.0
# Colores (skill dataviz, modo claro): una serie, sin leyenda
SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, GRID, SERIES = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6"


def stats(trades: pd.DataFrame, val: dict) -> dict:
    """Operaciones, aciertos, R medio, cuenta con 1 % por operación (orden de cierre), peor caída y veredicto."""
    v = make_verdict(trades, int(val["n_permutaciones"]), int(val["min_operaciones"]),
                     float(val["expectativa_minima_r"]))
    if trades.empty:
        return {"n": 0, "win": np.nan, "exp": np.nan, "ret": 0.0, "dd": 0.0, "v": v, "años": 0.0, "r_total": 0.0}
    t = trades.sort_values("exit_time")
    equity = np.cumprod(1 + RISK_PCT / 100 * t["r"].to_numpy())
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    years = max(0.5, (t["exit_time"].max() - t["entry_time"].min()).days / 365.25)
    return {"n": len(t), "win": float((t["r"] > 0).mean()), "exp": float(t["r"].mean()),
            "ret": float(equity[-1] - 1) * 100, "dd": float(((peak - equity) / peak).max()) * 100, "v": v,
            "años": years, "r_total": float(t["r"].sum())}


def max_concurrent(trades: pd.DataFrame) -> int:
    events = pd.concat([pd.Series(1, index=trades["entry_time"]), pd.Series(-1, index=trades["exit_time"])])
    return int(events.sort_index().cumsum().max())


def plot_equity(trades: pd.DataFrame, cutoff: pd.Timestamp, path):
    t = trades.sort_values("exit_time")
    cum = t["r"].cumsum().to_numpy()
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.axvspan(cutoff, t["exit_time"].max(), color=GRID, alpha=0.5, zorder=0)
    ax.annotate("prueba final", xy=(cutoff, 1), xycoords=("data", "axes fraction"), xytext=(6, -14),
                textcoords="offset points", fontsize=8, color=TEXT_SECONDARY)
    ax.axhline(0, color=TEXT_SECONDARY, linewidth=0.8)
    ax.plot(t["exit_time"], cum, color=SERIES, linewidth=2)
    ax.set_title("Cartera de 8 instrumentos: resultado acumulado (en R, después de costes)", loc="left",
                 fontsize=11, color=TEXT_PRIMARY)
    ax.set_ylabel("R acumulado (1 R = 1 % de la cuenta)", fontsize=8, color=TEXT_SECONDARY)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def main():
    from data_loader import load_from_dukascopy
    from report import REPORTS_DIR

    cfg = load_trading_config()
    sec, val = cfg["tendencia"], cfg["validacion"]
    p = Params.from_config(sec, float(cfg["costes"]["comision_por_lote"]))
    n_tests = int(sec["pruebas_previas"]) + 1
    cutoff = pd.Timestamp(val["inicio_prueba_final"], tz="UTC")

    parts = []
    for pair in sec["instrumentos"]:
        df = load_from_dukascopy(pair, sec["temporalidad"], years=float(sec["anios"]), refresh=False)
        t = simulate(df, pair, p)
        print(f"[v] {pair}: {len(df):,} velas [{df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}] · {len(t)} operaciones")
        parts.append(t)
    trades = pd.concat(parts, ignore_index=True)
    dev_t, fin_t = trades[trades["entry_time"] < cutoff], trades[trades["entry_time"] >= cutoff]
    dev, fin = stats(dev_t, val), stats(fin_t, val)
    ok = all(s["v"].label in PAPER_VERDICTS and s["ret"] > 0 for s in (dev, fin))
    real = ok and fin["v"].label == "VENTAJA CON EVIDENCIA" and fin["v"].p_value < 0.05 / n_tests

    out = REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}_tendencia"
    out.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out / "operaciones.csv", index=False)
    plot_equity(trades, cutoff, out / "cartera_r.png")

    def row(name, s):
        return (f"| {name} | {s['n']} ({s['n'] / s['años']:.0f}/año) | {s['win'] * 100:.0f} % | {s['exp']:+.3f} R | "
                f"{s['r_total']:+.1f} R | {s['ret']:+.1f} % | {s['dd']:.1f} % | {s['v'].p_value:.4f} | {s['v'].label} |")

    per_pair = []
    for pair, g in trades.groupby("pair"):
        d, f = g[g["entry_time"] < cutoff], g[g["entry_time"] >= cutoff]
        per_pair.append(f"| {pair} | {len(d)} · {d['r'].sum():+.1f} R | {len(f)} · {f['r'].sum():+.1f} R |")
    md = "\n".join([
        f"# Seguimiento de tendencia (Donchian 55/20) · cartera de 8 · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "", "Pre-registrado en `config.yaml` (sección `tendencia`, commit a496738) antes de programarlo. Una sola "
        "pasada. Costes de cada instrumento, comisión 7 USD/lote y swap 3 % anual en ambos sentidos.", "",
        "| periodo | operaciones | aciertos | R medio | R total | cuenta (1 % por op.) | peor caída | p | veredicto |",
        "|---|---|---|---|---|---|---|---|---|",
        row(f"Desarrollo (hasta {val['inicio_prueba_final']})", dev), row("Prueba final", fin), "",
        f"**Cumple el criterio para paper trading:** {'SÍ' if ok else 'NO'}. **Dinero real** (p < 0.05 / {n_tests} = "
        f"{0.05 / n_tests:.5f}): {'SÍ' if real else 'NO'}.", "",
        f"Posiciones abiertas a la vez, como máximo: {max_concurrent(trades)} (riesgo simultáneo hasta "
        f"{max_concurrent(trades) * RISK_PCT:.0f} %). Duración mediana: {trades['dias'].median():.0f} días.", "",
        "![cartera](cartera_r.png)", "", "## Por instrumento (solo información)", "",
        "| instrumento | desarrollo | prueba final |", "|---|---|---|", *per_pair])
    (out / "informe.md").write_text(md, encoding="utf-8")
    print("\n" + md + f"\n\n[v] Informe: {out / 'informe.md'}")


if __name__ == "__main__":
    main()
