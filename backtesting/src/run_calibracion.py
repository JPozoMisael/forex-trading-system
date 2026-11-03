"""
¿Se cumplen las probabilidades del modelo? Calibración fuera de muestra.

Para cada hora de la prueba final (desde validacion.inicio_prueba_final) se
reconstruye lo que dijo el modelo de ese momento (reentrenado cada 6 meses solo
con el pasado, igual que en run_instrumentos.py) y qué pasó después: si la
compra y la venta habrían tocado primero el objetivo, el stop o ninguno en el
horizonte. Se agrupa por probabilidad: si cuando dijo 45 % ganó ~45 % de las
veces, sus números son confiables.

    python backtesting/src/run_calibracion.py                    # instrumentos de produccion.activos
    python backtesting/src/run_calibracion.py --instrumentos XAU_USD

Solo MIDE: no cambia el modelo ni el umbral. Si la tabla sugiere otro umbral,
eso es una variante nueva y se valida con su propio protocolo pre-registrado.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/strategy-engine/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pandas as pd

from indicators import calculate_atr
from ml.labels import triple_barrier_outcomes
from shared.config import instrument_config, load_trading_config
from strategies.ml_strategy import MLStrategy
from validation import make_windows

# Tramos de probabilidad; el umbral de config.yaml (0.50) es un borde
BANDS = [0.0, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 1.0001]
SIDES = {"long": "Compra", "short": "Venta"}


def band_label(lo: float, hi: float) -> str:
    return f"{lo * 100:.0f} % o más" if hi > 1 else f"{lo * 100:.0f}–{hi * 100:.0f} %" if lo else f"menos de {hi * 100:.0f} %"


def oos_predictions(df: pd.DataFrame, cfg: dict, pair: str, log: Callable[[str], None] = print) -> pd.DataFrame:
    """
    p_long / p_short de cada vela de la prueba final, con el modelo entrenado
    SOLO con lo anterior a su ventana, y lo que pasó después (tp / sl / ninguno).
    """
    val, r, m, c = cfg["validacion"], cfg["riesgo"], cfg["modelo"], cfg["costes"]
    cutoff = pd.Timestamp(val["inicio_prueba_final"], tz="UTC")
    windows = make_windows(df.index, val["entrenamiento_inicial_anios"], int(val["ventana_prueba_meses"]),
                           first_test_start=cutoff)
    parts = []
    for w in windows:
        log(f"    ventana {w.n}/{len(windows)}: entrena hasta {w.test_start:%Y-%m-%d}, prueba hasta {w.test_end:%Y-%m-%d}")
        strategy = MLStrategy.train(df[df.index < w.test_start], cfg, pair)
        seen = df[df.index < w.test_end]
        proba = strategy.model.predict_proba(strategy._features(seen))
        parts.append(proba[proba.index >= w.test_start])
    proba = pd.concat(parts)

    atr = calculate_atr(df["high"], df["low"], df["close"], int(r["atr_periodo"]))
    outcomes = triple_barrier_outcomes(df, atr, int(m["horizonte_velas"]), float(r["sl_atr"]), float(r["tp_atr"]),
                                       pair, float(c["spread_pips"]), float(c["slippage_pips"]))
    out = proba.join(outcomes.rename(columns={"long": "res_long", "short": "res_short"}))
    return out.dropna()


def calibration_table(data: pd.DataFrame, rr: float) -> pd.DataFrame:
    """
    Por lado y tramo de probabilidad: horas, probabilidad media que dio el modelo
    y qué pasó de verdad. `R aprox.` = % TP × rr − % SL (las que no tocan nada
    cuentan 0), sin comisión: solo para comparar tramos.
    """
    rows = []
    for side, name in SIDES.items():
        p, res = data[f"p_{side}"], data[f"res_{side}"]
        for lo, hi in zip(BANDS[:-1], BANDS[1:]):
            mask = (p >= lo) & (p < hi)
            n = int(mask.sum())
            if not n:
                continue
            tp, sl = float((res[mask] == "tp").mean()), float((res[mask] == "sl").mean())
            rows.append({"operación": name, "probabilidad del modelo": band_label(lo, hi), "horas": n,
                         "dijo (media)": f"{p[mask].mean() * 100:.0f} %", "tocó objetivo": f"{tp * 100:.0f} %",
                         "tocó stop": f"{sl * 100:.0f} %", "ninguno en 48 velas": f"{max(0.0, 1 - tp - sl) * 100:.0f} %",
                         "R aprox.": f"{tp * rr - sl:+.2f}"})
    return pd.DataFrame(rows)


def run_pair(cfg: dict, pair: str, gran: str, log=print) -> pd.DataFrame:
    from data_loader import load_from_dukascopy

    icfg = instrument_config(cfg, pair, gran)
    df = load_from_dukascopy(pair, gran, years=icfg["mercado"]["anios_historia"])
    log(f"\n=== {pair} {gran}: {len(df):,} velas [{df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}] ===")
    return oos_predictions(df, icfg, pair, log)


def summary_md(tables: dict, cfg: dict, gran: str) -> str:
    r = cfg["riesgo"]
    rr = float(r["tp_atr"]) / float(r["sl_atr"])
    lines = [
        f"# ¿Se cumplen las probabilidades del modelo? · {gran} · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "",
        f"Prueba final desde {cfg['validacion']['inicio_prueba_final']}: cada hora, con el modelo reentrenado cada "
        f"{cfg['validacion']['ventana_prueba_meses']} meses solo con el pasado. Objetivo {r['tp_atr']} ATR, stop "
        f"{r['sl_atr']} ATR (objetivo = {rr:g} × stop), entrada a la apertura siguiente con spread y slippage del "
        f"instrumento. Umbral para operar: {cfg['modelo']['umbral_probabilidad'] * 100:.0f} %.",
        "",
        "**Cómo leerla:** si el modelo está calibrado, «tocó objetivo» se parece a «dijo». Con objetivo 1.5 × stop, "
        "empatar exige ganar ~40 % antes de costos (~45 % con costos, según config.yaml). «R aprox.» no incluye "
        "comisión y cuenta 0 las que no tocan nada.",
        "",
        "**Cuidado:** horas seguidas comparten casi el mismo futuro, así que no son pruebas independientes: "
        "10 000 horas valen bastante menos que 10 000 operaciones. Esta tabla describe; no autoriza a cambiar el "
        "umbral sin una prueba pre-registrada.",
    ]
    for pair, data in tables.items():
        lines += ["", f"## {pair} ({len(data):,} horas)", "", calibration_table(data, rr).to_markdown(index=False)]
    return "\n".join(lines)


def main():
    cfg = load_trading_config()
    parser = argparse.ArgumentParser(description="Calibración de las probabilidades del modelo (prueba final)")
    parser.add_argument("--instrumentos", default=",".join(cfg.get("produccion", {}).get("activos") or
                                                           [cfg["mercado"]["par"]]))
    parser.add_argument("--temporalidad", default=cfg["mercado"]["temporalidad"])
    args = parser.parse_args()
    gran = args.temporalidad.upper()

    tables = {}
    for pair in [p.strip().upper() for p in args.instrumentos.split(",") if p.strip()]:
        try:
            tables[pair] = run_pair(cfg, pair, gran)
        except Exception as e:  # un instrumento sin datos no detiene a los demás
            print(f"[x] {pair}: {e}")

    from report import REPORTS_DIR

    md = summary_md(tables, cfg, gran)
    out = REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}_calibracion.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print("\n" + md + f"\n\n[v] Informe: {out}")


if __name__ == "__main__":
    main()
