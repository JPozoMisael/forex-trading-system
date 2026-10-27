"""
Comparación de variantes del modelo SIN mirar la prueba final.

    python backtesting/src/run_experiments.py              # desarrollo: compara las variantes de config.yaml
    python backtesting/src/run_experiments.py --final v3_extendido_regularizado
                                                           # prueba final, UNA vez, con la variante elegida

Protocolo (config.yaml, secciones validacion y experimentos):
  1. Las variantes y la regla de selección se fijan antes de correr nada.
  2. Desarrollo: walk-forward solo con velas anteriores a `inicio_prueba_final`.
  3. Se elige la variante con la regla fijada (no a ojo).
  4. Prueba final: la elegida, v1 y las líneas base en el periodo final.
"""
import argparse
import copy
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
from run_validation import DISPLAY_NAMES, RULE_STRATEGIES, ml_factory, print_report
from shared.config import load_trading_config
from validation import validate

MIN_TRADES_SELECTION = 100


def variant_config(cfg: dict, overrides: dict) -> dict:
    """config.yaml con la sección `modelo` modificada por la variante (lightgbm se reemplaza entero)."""
    out = copy.deepcopy(cfg)
    out["modelo"].update(copy.deepcopy(overrides))
    return out


def select_variant(rows: list):
    """Regla fijada en config.yaml: mayor expectativa con >= 100 operaciones y expectativa > 0."""
    eligible = [r for r in rows if r["operaciones"] >= MIN_TRADES_SELECTION and r["expectativa_r"] > 0]
    return max(eligible, key=lambda r: r["expectativa_r"])["variante"] if eligible else None


def run_development(cfg, df, pair, gran):
    rows = []
    for name, overrides in cfg["experimentos"].items():
        print(f"\n=== Variante {name} (desarrollo) ===")
        vcfg = variant_config(cfg, overrides)
        report = validate(df, {"ml": ml_factory(df, vcfg, pair)}, pair, gran, vcfg, periodo="desarrollo",
                          log=lambda m: None)
        m, v = report.results["ml"].metrics, report.verdicts["ml"]
        rows.append({
            "variante": name, "retorno_%": m.total_return_pct, "max_dd_%": m.max_drawdown_pct,
            "sharpe": m.sharpe_ratio, "profit_factor": m.profit_factor, "operaciones": m.total_trades,
            "expectativa_r": round(v.expectancy_r, 4), "ic_bajo": round(v.ci_low, 4), "ic_alto": round(v.ci_high, 4),
            "p_valor": round(v.p_value, 4), "veredicto": v.label,
            "ventanas": len(report.windows),
            "periodo": f"{report.windows[0].test_start:%Y-%m-%d} a {report.windows[-1].test_end:%Y-%m-%d}",
        })
        print(f"    {m.total_return_pct:+.2f}% | {m.total_trades} ops | {v.expectancy_r:+.3f} R | {v.label}")

    table = pd.DataFrame(rows)
    chosen = select_variant(rows)
    print("\n" + "=" * 100)
    print(f"  DESARROLLO ({rows[0]['periodo']}, {rows[0]['ventanas']} ventanas) | {len(rows)} variantes probadas")
    print("=" * 100)
    print(table.drop(columns=["periodo", "ventanas"]).to_string(index=False))
    print(f"\nVariante elegida por la regla fijada: {chosen or 'NINGUNA (se mantiene v1_base)'}")

    out = REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}_experimentos_{pair}_{gran}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join([
        f"# Experimentos en DESARROLLO · {pair} {gran}",
        "",
        f"Periodo de prueba de desarrollo: {rows[0]['periodo']} ({rows[0]['ventanas']} ventanas). "
        f"La prueba final (desde {cfg['validacion']['inicio_prueba_final']}) no se usó.",
        f"Variantes probadas: {len(rows)} (todas registradas en config.yaml antes de correrlas).",
        f"Regla de selección: mayor expectativa con >= {MIN_TRADES_SELECTION} operaciones y expectativa > 0.",
        "",
        table.to_markdown(index=False),
        "",
        f"**Elegida:** {chosen or 'ninguna; se mantiene v1_base'}",
    ]), encoding="utf-8")
    print(f"[v] Tabla: {out}")
    return chosen


def run_final(cfg, df, pair, gran, chosen):
    factories = {"ml": ml_factory(df, variant_config(cfg, cfg["experimentos"]["v1_base"]), pair)}
    if chosen != "v1_base":
        factories[chosen] = ml_factory(df, variant_config(cfg, cfg["experimentos"][chosen]), pair)
    factories["ma_crossover"] = RULE_STRATEGIES["ma_crossover"]
    report = validate(df, factories, pair, gran, cfg, periodo="final")
    print_report(report)
    names = dict(DISPLAY_NAMES, ml="Modelo v1", **{chosen: f"Modelo {chosen}"})
    out_dir = write_report(report, names, main_strategy=chosen if chosen in factories else "ml")
    print(f"\n[v] Informe de la prueba final: {out_dir / 'informe.md'}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Experimentos sin mirar la prueba final")
    parser.add_argument("--final", metavar="VARIANTE", help="Evalúa UNA vez la variante elegida en la prueba final")
    args = parser.parse_args()

    cfg = load_trading_config()
    pair, gran = cfg["mercado"]["par"], cfg["mercado"]["temporalidad"]
    df = load_from_dukascopy(pair, gran, years=cfg["mercado"]["anios_historia"])
    print(f"[v] {pair} {gran}: {len(df):,} velas [{df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}]")

    if args.final:
        if args.final not in cfg["experimentos"]:
            sys.exit(f"[x] Variante desconocida: {args.final}")
        run_final(cfg, df, pair, gran, args.final)
    else:
        run_development(cfg, df, pair, gran)


if __name__ == "__main__":
    main()
