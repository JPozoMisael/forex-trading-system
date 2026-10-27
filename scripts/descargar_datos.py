"""
Descarga (o actualiza) el histórico de Dukascopy en data/ y revisa su calidad.

    python scripts/descargar_datos.py                          # EUR_USD H1, 6 años
    python scripts/descargar_datos.py --pair USD_JPY --years 5
    python scripts/descargar_datos.py --sin-descargar          # solo revisa la caché

La primera vez baja todo; después solo lo que falta desde la última vela guardada.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backtesting", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

from data_loader import dukascopy_cache_path, load_from_dukascopy
from shared.config import GRANULARITY_MINUTES


def revisar_calidad(df: pd.DataFrame, granularity: str) -> list:
    """Problemas encontrados en las velas (lista vacía = datos sanos)."""
    problemas = []
    if df.index.has_duplicates:
        problemas.append(f"{df.index.duplicated().sum()} velas duplicadas")
    if not df.index.is_monotonic_increasing:
        problemas.append("las velas no están en orden cronológico")
    imposibles = (df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))
    if imposibles.any():
        problemas.append(f"{int(imposibles.sum())} velas con high/low incoherentes")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        problemas.append("hay precios <= 0")

    # Huecos dentro de la semana (el mercado cierra viernes ~21:00 UTC y abre domingo ~21:00 UTC)
    bar = pd.Timedelta(minutes=GRANULARITY_MINUTES[granularity])
    saltos = df.index.to_series().diff()
    grandes = saltos[saltos > 6 * bar]
    entre_semana = grandes[~((grandes.index.dayofweek.isin([6, 0])) & (grandes <= pd.Timedelta(days=3)))]
    if len(entre_semana):
        problemas.append(
            f"{len(entre_semana)} huecos entre semana de más de {6 * bar} "
            f"(mayor: {entre_semana.max()} antes de {entre_semana.idxmax()}; suelen ser feriados)"
        )
    return problemas


def main():
    parser = argparse.ArgumentParser(description="Descarga histórico de Dukascopy a data/")
    parser.add_argument("--pair", default="EUR_USD")
    parser.add_argument("--granularity", default="H1")
    parser.add_argument("--years", type=float, default=6.0, help="Años a descargar la primera vez")
    parser.add_argument("--sin-descargar", action="store_true", help="Solo revisa la caché existente")
    args = parser.parse_args()
    gran = args.granularity.upper()

    df = load_from_dukascopy(args.pair, gran, years=args.years, refresh=not args.sin_descargar)
    if df.empty:
        sys.exit(f"[x] No hay datos de {args.pair} {gran}.")

    años = (df.index[-1] - df.index[0]).days / 365.25
    print(f"\n[v] {args.pair} {gran}: {len(df):,} velas | {df.index[0]:%Y-%m-%d %H:%M} a {df.index[-1]:%Y-%m-%d %H:%M} UTC ({años:.1f} años)")
    print(f"[i] Archivo: {dukascopy_cache_path(args.pair, gran)}")
    if "spread_pips" in df:
        s = df["spread_pips"]
        print(f"[i] Spread Dukascopy al cierre (pips): mediana {s.median():.2f} | p95 {s.quantile(0.95):.2f} | máx {s.max():.1f}")
        print("    Es spread institucional: el backtest usa uno de broker minorista (más alto).")

    problemas = revisar_calidad(df, gran)
    if problemas:
        print("[!] Revisión de calidad:")
        for p in problemas:
            print(f"    - {p}")
    else:
        print("[v] Revisión de calidad: sin problemas.")


if __name__ == "__main__":
    main()
