"""
Prueba ÚNICA de los setups de trader pre-registrados en config.yaml (sección
`setups`, 2026-11-03).

    python backtesting/src/run_setups.py

Cada setup × instrumento se valida con el mismo motor y veredicto que el resto
del proyecto (costes, permutación contra la operación opuesta, bootstrap), por
separado en el DESARROLLO y en la PRUEBA FINAL. Aplica el criterio escrito en el
pre-registro y escribe reports/<fecha>_setups/informe.md con gráficos de
operaciones de ejemplo (una ganadora y una perdedora por setup).
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(ROOT, "services/strategy-engine/src"))
sys.path.insert(0, os.path.join(ROOT, "services/risk-manager/src"))
sys.path.insert(0, os.path.join(ROOT, "services/monitoring/src"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pandas as pd

from run_instrumentos import PAPER_VERDICTS
from shared.config import instrument_config, load_trading_config
from strategies.setups import setup_factory, setup_signals
from validation import BUY_AND_HOLD, validate

DISPLAY = {"fibonacci": "Retroceso de Fibonacci", "ema50": "Rebote en EMA 50",
           "ruptura_londres": "Ruptura del máximo de ayer (Londres)", "bollinger_rsi": "Bollinger + RSI"}
PERIODS = {"desarrollo": "Desarrollo (2012–2023)", "final": "Prueba final (2023–2026)"}


def period_row(report, name: str) -> dict:
    m, v = report.results[name].metrics, report.verdicts[name]
    return {"retorno_%": round(m.total_return_pct, 2),
            "buy_hold_%": round(report.results[BUY_AND_HOLD].metrics.total_return_pct, 2),
            "operaciones": m.total_trades, "win_rate_%": round(m.win_rate_pct, 1),
            "expectativa_r": round(v.expectancy_r, 3) if pd.notna(v.expectancy_r) else float("nan"),
            "p_valor": round(v.p_value, 4) if pd.notna(v.p_value) else float("nan"),
            "max_dd_%": round(m.max_drawdown_pct, 2), "veredicto": v.label}


def eligible(dev: dict, fin: dict) -> bool:
    """Pre-registro: veredicto positivo y retorno neto > 0 en LOS DOS periodos."""
    return all(r["veredicto"] in PAPER_VERDICTS and r["retorno_%"] > 0 for r in (dev, fin))


def real_money(dev: dict, fin: dict, n_tests: int) -> bool:
    return eligible(dev, fin) and fin["veredicto"] == "VENTAJA CON EVIDENCIA" and fin["p_valor"] < 0.05 / n_tests


def example_charts(pair: str, df: pd.DataFrame, trades: pd.DataFrame, out_dir: Path, key: str) -> list:
    """Una operación ganadora (TP) y una perdedora (SL) del setup, dibujadas como en el correo de cierre."""
    from charts import plot_trade

    files = []
    if trades.empty:
        return files
    for reason, label in (("tp", "ganadora"), ("sl", "perdedora")):
        pick = trades[trades["exit_reason"] == reason]
        if pick.empty:
            continue
        t = pick.iloc[len(pick) // 2]  # una del medio, no la mejor ni la peor
        i0, i1 = df.index.get_loc(t["entry_time"]), df.index.get_loc(t["exit_time"])
        window = df.iloc[max(0, i0 - 60): min(len(df), i1 + 12)]
        png = plot_trade(pair, window, "H1", {
            "direction": t["side"], "entry_price": t["entry_price"], "exit_price": t["exit_price"],
            "opened_at": t["entry_time"], "closed_at": t["exit_time"], "stop_loss": t["stop_loss"],
            "take_profit": t["take_profit"], "pnl": t["pnl"]})
        name = f"{key}_{label}.png"
        (out_dir / name).write_bytes(png)
        files.append((label, name, t))
    return files


def main():
    from data_loader import load_from_dukascopy
    from report import REPORTS_DIR

    cfg = load_trading_config()
    sec = cfg["setups"]
    gran = sec["temporalidad"]
    n_tests = int(sec["pruebas_previas"]) + len(sec["nombres"]) * len(sec["instrumentos"])
    out_dir = REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}_setups"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, examples = [], {}
    for pair in sec["instrumentos"]:
        icfg = instrument_config(cfg, pair, gran)
        icfg["costes"].update(sec.get("costes") or {})
        icfg["riesgo"]["max_velas_abierta"] = int(sec["max_velas_abierta"])
        df = load_from_dukascopy(pair, gran, years=icfg["mercado"]["anios_historia"], refresh=False)
        print(f"\n=== {pair} {gran}: {len(df):,} velas [{df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}] ===")
        signals = {name: setup_signals(df, name) for name in sec["nombres"]}
        for name in sec["nombres"]:
            res = {}
            for periodo in PERIODS:
                report = validate(df, {name: setup_factory(name, signals, name)}, pair, gran, icfg,
                                  periodo=periodo, log=lambda m: None)
                res[periodo] = period_row(report, name)
                if periodo == "final":
                    examples[(name, pair)] = example_charts(pair, df, report.results[name].trades, out_dir,
                                                            f"{name}_{pair}")
            dev, fin = res["desarrollo"], res["final"]
            rows.append({"setup": name, "instrumento": pair, "dev": dev, "final": fin,
                         "paper": eligible(dev, fin), "real": real_money(dev, fin, n_tests)})
            print(f"    {name:16s} desarrollo {dev['retorno_%']:+7.2f}% {dev['operaciones']:4d} ops "
                  f"{dev['expectativa_r']:+.3f} R {dev['veredicto']} | final {fin['retorno_%']:+7.2f}% "
                  f"{fin['operaciones']:4d} ops {fin['expectativa_r']:+.3f} R p={fin['p_valor']} {fin['veredicto']}")

    md = report_md(rows, examples, cfg, n_tests)
    (out_dir / "informe.md").write_text(md, encoding="utf-8")
    print("\n" + md + f"\n\n[v] Informe: {out_dir / 'informe.md'}")


def report_md(rows: list, examples: dict, cfg: dict, n_tests: int) -> str:
    def fmt(r):
        return (f"{r['retorno_%']:+.1f} % · {r['operaciones']} ops · {r['win_rate_%']:.0f} % gan. · "
                f"{r['expectativa_r']:+.3f} R · {r['veredicto']}")

    table = pd.DataFrame([{
        "setup": DISPLAY[r["setup"]], "instrumento": r["instrumento"],
        PERIODS["desarrollo"]: fmt(r["dev"]), PERIODS["final"]: fmt(r["final"]),
        "p (final)": r["final"]["p_valor"], "paper": "SÍ" if r["paper"] else "no",
        "dinero real": "SÍ" if r["real"] else "no"} for r in rows])
    winners = [f"{DISPLAY[r['setup']]} en {r['instrumento']}" for r in rows if r["paper"]]
    lines = [
        f"# Prueba de setups de trader · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
        "Pre-registrados en `config.yaml` (sección `setups`, commit 8c7b0f9) antes de programarlos. Una sola pasada. "
        "Costes del instrumento + swap 3 % anual; salida por tiempo a las 120 velas.", "",
        f"**Criterio:** veredicto positivo y retorno neto > 0 en el desarrollo **y** en la prueba final. Para dinero real, "
        f"además VENTAJA CON EVIDENCIA en la final con p < 0.05 / {n_tests} = {0.05 / n_tests:.5f}.", "",
        table.to_markdown(index=False), "",
        "**Cumplen el criterio para paper trading:** " + (", ".join(winners) or "ninguno"), "",
        "«R» = resultado medio por operación en unidades de riesgo, después de costes. «gan.» = % de operaciones "
        "ganadoras. Con objetivo 2 × riesgo, empatar exige ganar ~33 % antes de costes.", "",
        "## Operaciones de ejemplo (prueba final)", "",
        "Una ganadora y una perdedora de cada setup, elegidas del medio (no la mejor ni la peor).", ""]
    for (name, pair), files in examples.items():
        if not files:
            continue
        lines += [f"### {DISPLAY[name]} · {pair}", ""]
        for label, fname, t in files:
            lines += [f"**{label.capitalize()}** ({t['side']}, {pd.Timestamp(t['entry_time']):%Y-%m-%d %H:%M} UTC, "
                      f"{t['pnl']:+,.2f} USD):", "", f"![{label}]({fname})", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
