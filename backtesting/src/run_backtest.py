"""
Script ejecutable por línea de comandos para correr backtests rápidos.
Uso:
    python backtesting/src/run_backtest.py --pair EUR_USD --strategy ma_crossover --capital 10000 --risk 1.0
"""
import argparse
import sys
import os

# Configurar encoding seguro para Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ajustar rutas de import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/strategy-engine/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/risk-manager/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from data_loader import load_from_db, load_from_csv, generate_synthetic_forex_data
from engine import BacktestEngine
from strategies.ma_crossover import MACrossoverStrategy
from strategies.mean_reversion import MeanReversionStrategy


def print_banner():
    print("=" * 70)
    print("  [*] FOREX QUANTITATIVE BACKTESTING SYSTEM")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Simulador de Backtesting Forex")
    parser.add_argument("--pair", type=str, default="EUR_USD", help="Par de divisas (ej: EUR_USD, USD_JPY)")
    parser.add_argument(
        "--strategy",
        type=str,
        default="ma_crossover",
        choices=["ma_crossover", "mean_reversion"],
        help="Estrategia a simular",
    )
    parser.add_argument("--capital", type=float, default=10000.0, help="Capital inicial en USD")
    parser.add_argument("--risk", type=float, default=1.0, help="Riesgo porcentual por operación (%)")
    parser.add_argument("--spread", type=float, default=1.2, help="Spread simulado en pips")
    parser.add_argument("--candles", type=int, default=2000, help="Cantidad de velas para la prueba")
    parser.add_argument("--csv", type=str, default=None, help="Ruta opcional a archivo CSV de velas")

    args = parser.parse_args()
    print_banner()

    # 1. Cargar Datos
    df = None
    if args.csv and os.path.exists(args.csv):
        print(f"[+] Cargando velas desde CSV: {args.csv}...")
        df = load_from_csv(args.csv)
    else:
        print(f"[+] Intentando cargar velas desde base de datos TimescaleDB para {args.pair}...")
        df = load_from_db(pair=args.pair, limit=args.candles)

    if df is None or len(df) < 50:
        print(f"[i] Base de datos no conectada o vacia. Generando {args.candles} velas sinteticas...")
        df = generate_synthetic_forex_data(pair=args.pair, n_candles=args.candles)

    print(f"[v] Datos cargados: {len(df)} velas [{df.index[0]} a {df.index[-1]}]\n")

    # 2. Instanciar Estrategia
    if args.strategy == "ma_crossover":
        strategy = MACrossoverStrategy(fast_period=9, slow_period=21, trend_period=100, use_trend_filter=True)
    else:
        strategy = MeanReversionStrategy(bb_period=20, bb_std=2.0, rsi_period=14)

    print(f"[>] Estrategia: {strategy.name}")
    print(f"[>] Capital: ${args.capital:,.2f} | Riesgo: {args.risk}% | Spread: {args.spread} pips\n")

    # 3. Ejecutar Backtest
    engine = BacktestEngine(
        initial_balance=args.capital,
        risk_per_trade_pct=args.risk,
        spread_pips=args.spread,
    )

    result = engine.run(strategy=strategy, df=df, pair=args.pair)

    # 4. Mostrar Resultados
    print("=" * 70)
    print(f"  [#] REPORTE DE RENDIMIENTO | {result.strategy_name} ({result.pair})")
    print("=" * 70)

    metrics_dict = result.metrics.to_dict()
    for k, v in metrics_dict.items():
        print(f"  {k:<35} : {v:>25}")

    print("=" * 70)

    if not result.trades_df.empty:
        print("\n[+] MUESTRA DE LAS ULTIMAS 5 OPERACIONES:")
        print("-" * 70)
        sample = result.trades_df.tail(5)[["trade_id", "side", "entry_price", "exit_price", "pnl", "pips", "exit_reason"]]
        print(sample.to_string(index=False))
        print("-" * 70)

    print("\n[v] Backtest completado exitosamente.\n")


if __name__ == "__main__":
    main()
