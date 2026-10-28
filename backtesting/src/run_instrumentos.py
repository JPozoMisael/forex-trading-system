"""
Prueba final del modelo v1 en varios instrumentos y temporalidades, UNA vez,
con el protocolo pre-registrado en config.yaml (sección multi_instrumento).

    python backtesting/src/run_instrumentos.py                       # todo lo de config.yaml
    python backtesting/src/run_instrumentos.py --instrumentos XAU_USD --temporalidades H1

Para cada combinación: descarga la historia de Dukascopy, valida walk-forward
en el periodo final con los costes del instrumento y escribe su informe. Al
final, una tabla con el veredicto, el p-valor corregido por el número de
pruebas y si cumple la regla para paper trading.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/strategy-engine/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/risk-manager/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pandas as pd

from data_loader import load_from_dukascopy
from report import REPORTS_DIR, write_report
from run_validation import DISPLAY_NAMES, ml_factory
from shared.config import instrument_config, load_trading_config
from validation import BUY_AND_HOLD, validate

PAPER_VERDICTS = ("VENTAJA CON EVIDENCIA", "POSITIVO PERO NO CONCLUYENTE")


def paper_eligible(row: dict) -> bool:
    """Regla pre-registrada para paper trading: veredicto positivo y retorno neto > 0."""
    return row["veredicto"] in PAPER_VERDICTS and row["retorno_%"] > 0


def real_money_eligible(row: dict, n_tests: int) -> bool:
    """Para dinero real: VENTAJA CON EVIDENCIA con p < 0.05 / número de pruebas (Bonferroni)."""
    return row["veredicto"] == "VENTAJA CON EVIDENCIA" and row["p_valor"] < 0.05 / max(n_tests, 1)


def run_one(cfg: dict, pair: str, gran: str) -> dict:
    icfg = instrument_config(cfg, pair, gran)
    df = load_from_dukascopy(pair, gran, years=icfg["mercado"]["anios_historia"])
    print(f"\n=== {pair} {gran}: {len(df):,} velas [{df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}] | "
          f"spread {icfg['costes']['spread_pips']} + slippage {icfg['costes']['slippage_pips']} pips ===")
    report = validate(df, {"ml": ml_factory(df, icfg, pair)}, pair, gran, icfg, periodo="final", log=lambda m: None)
    m, v = report.results["ml"].metrics, report.verdicts["ml"]
    out_dir = write_report(report, dict(DISPLAY_NAMES, ml=f"Modelo {pair} {gran}"), main_strategy="ml")
    row = {
        "instrumento": pair, "temporalidad": gran,
        "retorno_%": round(m.total_return_pct, 2),
        "buy_hold_%": round(report.results[BUY_AND_HOLD].metrics.total_return_pct, 2),
        "max_dd_%": round(m.max_drawdown_pct, 2), "profit_factor": round(m.profit_factor, 2),
        "operaciones": m.total_trades, "win_rate_%": round(m.win_rate_pct, 1),
        "expectativa_r": round(v.expectancy_r, 3), "ic_bajo": round(v.ci_low, 3), "ic_alto": round(v.ci_high, 3),
        "p_valor": round(v.p_value, 4), "veredicto": v.label, "informe": str(out_dir / "informe.md"),
    }
    print(f"    {row['retorno_%']:+.2f}% (B&H {row['buy_hold_%']:+.2f}%) | {row['operaciones']} ops | "
          f"{row['expectativa_r']:+.3f} R | p={row['p_valor']} | {row['veredicto']}")
    return row


def summarize(rows: list, cfg: dict) -> str:
    n = len(rows)
    table = pd.DataFrame(rows)
    table["paper"] = [("SÍ" if paper_eligible(r) else "no") for r in rows]
    table["dinero_real"] = [("SÍ" if real_money_eligible(r, n) else "no") for r in rows]
    winners = table[table["paper"] == "SÍ"]
    lines = [
        f"# Prueba final multi-instrumento · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "",
        f"Modelo v1_base sin ajustes por instrumento. Periodo final desde {cfg['validacion']['inicio_prueba_final']}. "
        f"{n} combinaciones probadas: con p < 0.05 cabría esperar ~{n * 0.05:.1f} positivas por puro azar, "
        f"por eso para dinero real se exige p < {0.05 / n:.4f}.",
        "",
        table.drop(columns=["informe"]).to_markdown(index=False),
        "",
        "**Cumplen la regla para paper trading:** "
        + (", ".join(f"{r.instrumento} {r.temporalidad}" for r in winners.itertuples()) or "ninguna"),
        "",
        "Informes por combinación:",
        *[f"- {r['instrumento']} {r['temporalidad']}: {r['informe']}" for r in rows],
    ]
    return "\n".join(lines)


def main():
    cfg = load_trading_config()
    mi = cfg["multi_instrumento"]
    parser = argparse.ArgumentParser(description="Prueba final del modelo en varios instrumentos")
    parser.add_argument("--instrumentos", default=",".join(mi["instrumentos"]))
    parser.add_argument("--temporalidades", default=",".join(mi["temporalidades"]))
    args = parser.parse_args()

    rows = []
    for gran in [g.strip().upper() for g in args.temporalidades.split(",") if g.strip()]:
        for pair in [p.strip().upper() for p in args.instrumentos.split(",") if p.strip()]:
            try:
                rows.append(run_one(cfg, pair, gran))
            except Exception as e:  # un instrumento sin datos no detiene a los demás
                print(f"[x] {pair} {gran}: {e}")

    md = summarize(rows, cfg)
    out = REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}_instrumentos.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print("\n" + md + f"\n\n[v] Resumen: {out}")


if __name__ == "__main__":
    main()
