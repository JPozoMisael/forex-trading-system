"""
Validación walk-forward fuera de muestra, con líneas base y veredicto.

    python backtesting/src/run_validation.py                       # estrategias de reglas
    python backtesting/src/run_validation.py --strategies ma_crossover

Lee par, temporalidad, costos, riesgo y ventanas de config.yaml.
"""
import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/strategy-engine/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/risk-manager/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pandas as pd

from data_loader import load_from_dukascopy
from shared.config import load_trading_config
from strategies.ma_crossover import MACrossoverStrategy
from strategies.mean_reversion import MeanReversionStrategy
from validation import ValidationReport, validate

RULE_STRATEGIES = {
    "ma_crossover": lambda train, w: MACrossoverStrategy(fast_period=9, slow_period=21, trend_period=200, use_trend_filter=True),
    "mean_reversion": lambda train, w: MeanReversionStrategy(bb_period=20, bb_std=2.0, rsi_period=14),
}


def print_report(report: ValidationReport):
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print("\n" + "=" * 100)
    print(f"  VALIDACIÓN WALK-FORWARD | {report.pair} {report.granularity} | {len(report.windows)} ventanas | "
          f"costos: spread {report.costs['spread_pips']} + slippage {report.costs['slippage_pips']} pips, "
          f"${report.costs['commission_per_lot']}/lote")
    print("=" * 100)

    summary = []
    for name, res in report.results.items():
        m = res.metrics
        summary.append({
            "estrategia": name,
            "retorno_%": m.total_return_pct,
            "max_dd_%": m.max_drawdown_pct,
            "sharpe": m.sharpe_ratio,
            "profit_factor": m.profit_factor,
            "win_rate_%": m.win_rate_pct,
            "operaciones": m.total_trades,
        })
    print("\nTotal fuera de muestra (saldo encadenado entre ventanas):")
    print(pd.DataFrame(summary).to_string(index=False))

    print("\nRetorno % por ventana:")
    table = pd.DataFrame({name: [r["retorno_pct"] for r in res.per_window] for name, res in report.results.items()})
    first = next(iter(report.results.values()))
    table.index = [f"{r['ventana']}: {r['desde']:%Y-%m}→{r['hasta']:%Y-%m}" for r in first.per_window]
    print(table.to_string())

    print("\nVeredicto (permutación con la operación opuesta + bootstrap de la expectativa):")
    for name, v in report.verdicts.items():
        print(f"  - {name}: {v.label} | {v.n_trades} ops | expectativa {v.expectancy_r:+.3f} R | "
              f"IC95% [{v.ci_low:+.3f}, {v.ci_high:+.3f}] | p = {v.p_value:.4f}")
        print(f"      {v.explanation}")


def main():
    parser = argparse.ArgumentParser(description="Validación walk-forward fuera de muestra")
    parser.add_argument("--strategies", default="ma_crossover,mean_reversion",
                        help=f"Separadas por coma: {', '.join(RULE_STRATEGIES)}")
    args = parser.parse_args()

    cfg = load_trading_config()
    pair, gran = cfg["mercado"]["par"], cfg["mercado"]["temporalidad"]
    df = load_from_dukascopy(pair, gran, years=cfg["mercado"]["anios_historia"])
    print(f"[v] {pair} {gran}: {len(df)} velas [{df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}]")

    factories = {name: RULE_STRATEGIES[name] for name in args.strategies.split(",")}
    report = validate(df, factories, pair, gran, cfg)
    print_report(report)


if __name__ == "__main__":
    main()
