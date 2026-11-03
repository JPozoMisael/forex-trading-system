"""
Estudio de patrones con las herramientas de TradingView: ¿qué tenían en común
las operaciones que tocaron el objetivo, en el momento de entrar?

SOLO usa el periodo de DESARROLLO (antes de validacion.inicio_prueba_final):
aquí se buscan ideas; comprobarlas es otro paso, pre-registrado.

    python backtesting/src/run_patrones.py                      # instrumentos de produccion.activos
    python backtesting/src/run_patrones.py --instrumentos XAU_USD

Para cada hora se plantea la compra y la venta del modelo (stop 2 ATR, objetivo
3 ATR, 48 velas) y se mira qué pasó. Tres partes:

1. Patrón por patrón: con cada herramienta (tendencia H4, Fibonacci, RSI...)
   cuánto ganaron compras y ventas en cada situación, en DOS épocas distintas.
   Un patrón real se repite en las dos; uno casual, no. Como control, se cuenta
   cuántos "se repiten" si se desordena el futuro: eso es lo que da el azar.
2. Machine learning: el modelo actual (base) contra el mismo modelo con las
   herramientas de TradingView, walk-forward dentro del desarrollo. ¿Separa
   mejor las horas buenas de las malas?
3. Qué herramientas usó más el modelo (contribuciones exactas de LightGBM).

Escribe reports/<fecha>_patrones/informe.md con sus gráficos.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/strategy-engine/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import copy

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from indicators import calculate_atr
from ml.labels import triple_barrier_outcomes
from ml.tradingview import CATEGORICAL, TV_COLUMNS, TV_WARMUP_BARS, tradingview_features
from shared.config import instrument_config, load_trading_config
from strategies.ml_strategy import MLStrategy
from validation import make_windows

SIDES = {"long": "Compra", "short": "Venta"}

# Un patrón "se repite" si en las dos épocas gana (o pierde) al menos MIN_EDGE puntos
# frente a lo normal de esa época, con MIN_HOURS horas en cada una.
MIN_EDGE = 0.03
MIN_HOURS = 300
N_CONTROLS = 20

BASE_NAMES = {
    "ret_1": "Cambio de la última hora", "ret_3": "Cambio de 3 horas", "ret_6": "Cambio de 6 horas",
    "ret_12": "Cambio de 12 horas", "ret_24": "Cambio de 24 horas", "atr_pct": "Volatilidad (ATR / precio)",
    "rsi_14": "RSI 14 (modelo base)", "dist_ema_20": "Distancia a la EMA 20", "dist_ema_50": "Distancia a la EMA 50",
    "dist_ema_200": "Distancia a la EMA 200", "vol_24": "Volatilidad de 24 h", "vol_120": "Volatilidad de 5 días",
    "vol_ratio": "Volatilidad 24 h / 5 días", "range_atr": "Tamaño de la última vela",
    "body_atr": "Cuerpo de la última vela", "pos_rango_24": "Posición en el rango de 24 h",
    "hora_sin": "Hora del día", "hora_cos": "Hora del día (2)", "dia_semana": "Día de la semana",
}
CATEGORY_NAMES = {
    "sesion": {0: "Asia", 1: "Londres", 2: "Londres + Nueva York", 3: "Nueva York"},
    "fib_mov": {1: "subida", -1: "bajada"},
    "vela_envolvente": {1: "envolvente alcista", -1: "envolvente bajista", 0: "ninguna"},
    "vela_martillo": {1: "martillo", -1: "estrella fugaz", 0: "ninguna"},
}
TREND_NAMES = {1: "alcista", -1: "bajista", 0: "mixta"}

# Colores (skill dataviz, modo claro; paleta validada: azul y naranja)
SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, GRID, REFERENCE = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#8a8984"
SERIES = {"base": "#2a78d6", "tradingview": "#eb6834"}
MODEL_NAMES = {"base": "Modelo actual", "tradingview": "Modelo + TradingView"}


# ---------------------------------------------------------------- datos

def study_data(df: pd.DataFrame, cfg: dict, pair: str) -> pd.DataFrame:
    """Herramientas de TradingView y resultado (tp/sl/ninguno) de cada hora del DESARROLLO."""
    r, m, c = cfg["riesgo"], cfg["modelo"], cfg["costes"]
    cutoff = pd.Timestamp(cfg["validacion"]["inicio_prueba_final"], tz="UTC")
    atr = calculate_atr(df["high"], df["low"], df["close"], int(r["atr_periodo"]))
    outcomes = triple_barrier_outcomes(df, atr, int(m["horizonte_velas"]), float(r["sl_atr"]), float(r["tp_atr"]),
                                       pair, float(c["spread_pips"]), float(c["slippage_pips"]))
    data = tradingview_features(df, int(r["atr_periodo"])).join(
        outcomes.rename(columns={"long": "res_long", "short": "res_short"}))
    data = data.iloc[TV_WARMUP_BARS:]
    return data[data.index < cutoff].dropna(subset=["res_long", "res_short"])


def split_epochs(data: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Dos épocas de igual duración: la primera mitad del desarrollo y la segunda."""
    mid = data.index[0] + (data.index[-1] - data.index[0]) / 2
    return data[data.index < mid], data[data.index >= mid]


# ---------------------------------------------------------------- 1. patrón por patrón

def _fmt(v: float) -> str:
    return f"{v:.2f}" if abs(v) < 10 else f"{v:.0f}"


def bin_column(col: str, a: pd.Series, values: pd.Series) -> pd.Series:
    """Situación de cada hora: la categoría, o el quintil (cortes de la PRIMERA época)."""
    if col in CATEGORICAL:
        names = CATEGORY_NAMES.get(col)
        if col.startswith("tend_"):
            names = TREND_NAMES
        if names:
            return values.map(lambda v: names.get(int(v), str(v)) if pd.notna(v) else np.nan)
        return values.map(lambda v: f"{int(v):+d}" if pd.notna(v) else np.nan)
    edges = np.unique(a.quantile([0, 0.2, 0.4, 0.6, 0.8, 1]).to_numpy())
    if len(edges) < 3:
        return pd.Series(np.nan, index=values.index)
    inner = edges[1:-1]
    labels = [f"menos de {_fmt(inner[0])}"] + [f"{_fmt(lo)} a {_fmt(hi)}" for lo, hi in zip(inner[:-1], inner[1:])] \
        + [f"{_fmt(inner[-1])} o más"]
    return pd.cut(values, [-np.inf, *inner, np.inf], labels=labels, right=False).astype(object)


def pattern_rows(a: pd.DataFrame, b: pd.DataFrame, res_b: Dict[str, pd.Series] = None) -> List[dict]:
    """
    Una fila por herramienta, situación y lado: ventaja frente a lo normal de cada
    época (puntos de % que tocaron el objetivo). `res_b` reemplaza los resultados
    de la segunda época (para el control con el futuro desordenado).
    """
    rows = []
    for col in TV_COLUMNS:
        bins_a, bins_b = bin_column(col, a[col], a[col]), bin_column(col, a[col], b[col])
        for side, name in SIDES.items():
            win_a = (a[f"res_{side}"] == "tp").astype(float)
            win_b = ((res_b[side] if res_b else b[f"res_{side}"]) == "tp").astype(float)
            base_a, base_b = win_a.mean(), win_b.mean()
            ga, gb = win_a.groupby(bins_a), win_b.groupby(bins_b)
            stats_a, stats_b = ga.agg(["mean", "size"]), gb.agg(["mean", "size"])
            for label in stats_a.index:
                if label not in stats_b.index:
                    continue
                rows.append({"herramienta": col, "situación": label, "lado": name,
                             "horas_1": int(stats_a.loc[label, "size"]), "horas_2": int(stats_b.loc[label, "size"]),
                             "gana_1": stats_a.loc[label, "mean"], "gana_2": stats_b.loc[label, "mean"],
                             "ventaja_1": stats_a.loc[label, "mean"] - base_a,
                             "ventaja_2": stats_b.loc[label, "mean"] - base_b})
    return rows


def repeated(rows: List[dict]) -> List[dict]:
    """Patrones que ganan (o pierden) lo mismo en las dos épocas, con horas suficientes."""
    out = [r for r in rows if r["horas_1"] >= MIN_HOURS and r["horas_2"] >= MIN_HOURS
           and np.sign(r["ventaja_1"]) == np.sign(r["ventaja_2"])
           and min(abs(r["ventaja_1"]), abs(r["ventaja_2"])) >= MIN_EDGE]
    return sorted(out, key=lambda r: -min(abs(r["ventaja_1"]), abs(r["ventaja_2"])))


def chance_count(a: pd.DataFrame, b: pd.DataFrame, n: int = N_CONTROLS, seed: int = 3) -> float:
    """
    Cuántos patrones "se repiten" por puro azar: se desplaza el futuro de la
    segunda época una cantidad aleatoria (rotación circular, conserva las rachas)
    para que ya no corresponda a sus horas, y se cuentan. Promedio de `n` intentos.
    """
    rng = np.random.default_rng(seed)
    counts = []
    for _ in range(n):
        k = int(rng.integers(len(b) // 5, len(b) * 4 // 5))
        res_b = {side: pd.Series(np.roll(b[f"res_{side}"].to_numpy(), k), index=b.index) for side in SIDES}
        counts.append(len(repeated(pattern_rows(a, b, res_b))))
    return float(np.mean(counts))


# ---------------------------------------------------------------- 2. y 3. machine learning

def ml_oos(df: pd.DataFrame, cfg: dict, pair: str, conjunto: str,
           log: Callable[[str], None] = print) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Walk-forward DENTRO del desarrollo con el conjunto de datos `conjunto`:
    probabilidades fuera de muestra y aporte medio (|contribución|) de cada dato.
    """
    cutoff = pd.Timestamp(cfg["validacion"]["inicio_prueba_final"], tz="UTC")
    dev = df[df.index < cutoff]
    c = copy.deepcopy(cfg)
    c["modelo"]["features"] = conjunto
    val = c["validacion"]
    windows = make_windows(dev.index, val["entrenamiento_inicial_anios"], int(val["ventana_prueba_meses"]))
    probas, contribs = [], []
    for w in windows:
        log(f"    {MODEL_NAMES[conjunto]}: ventana {w.n}/{len(windows)} (prueba {w.test_start:%Y-%m})")
        strategy = MLStrategy.train(dev[dev.index < w.test_start], c, pair)
        feats = strategy._features(dev[dev.index < w.test_end])
        test = feats[feats.index >= w.test_start]
        probas.append(strategy.model.predict_proba(test))
        X = test[strategy.model.feature_columns]
        for clf in (strategy.model.long, strategy.model.short):
            contribs.append(pd.DataFrame(np.abs(clf.predict(X, pred_contrib=True)[:, :-1]),
                                         columns=strategy.model.feature_columns).mean())
    return pd.concat(probas), pd.concat(contribs, axis=1).mean(axis=1).sort_values(ascending=False)


def separation(proba: pd.DataFrame, data: pd.DataFrame, threshold: float) -> List[dict]:
    """Por lado: cuánto ganan el 10 % de horas que el modelo ve mejor, el 10 % peor y las que operaría."""
    joined = proba.join(data[["res_long", "res_short"]], how="inner")
    rows = []
    for side, name in SIDES.items():
        p, win = joined[f"p_{side}"], (joined[f"res_{side}"] == "tp").astype(float)
        top, bottom = p >= p.quantile(0.9), p <= p.quantile(0.1)
        chosen = p >= threshold
        rows.append({"lado": name, "todas": win.mean(), "mejor_10": win[top].mean(), "peor_10": win[bottom].mean(),
                     "señales": int(chosen.sum()), "gana_señales": win[chosen].mean() if chosen.any() else np.nan})
    return rows


def calibration_curve(proba: pd.DataFrame, data: pd.DataFrame, side: str, bins: int = 10) -> pd.DataFrame:
    """Deciles de lo que dijo el modelo: probabilidad media y % que de verdad tocó el objetivo."""
    joined = proba.join(data[[f"res_{side}"]], how="inner")
    p, win = joined[f"p_{side}"], (joined[f"res_{side}"] == "tp").astype(float)
    q = pd.qcut(p, bins, labels=False, duplicates="drop")
    return pd.DataFrame({"dijo": p.groupby(q).mean(), "real": win.groupby(q).mean()})


# ---------------------------------------------------------------- gráficos

def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, linewidth=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)


def plot_calibration(pair: str, curves: Dict[str, Dict[str, pd.DataFrame]], path: Path):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), dpi=150, sharey=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, (side, name) in zip(axes, SIDES.items()):
        _style(ax)
        all_vals = [v for cs in curves.values() for v in (cs[side]["dijo"].tolist() + cs[side]["real"].tolist())]
        lo, hi = min(all_vals) - 0.02, max(all_vals) + 0.02
        ax.plot([lo, hi], [lo, hi], color=REFERENCE, linewidth=1, linestyle=(0, (3, 3)), zorder=1)
        ax.annotate("lo que diría un modelo calibrado", xy=(hi, hi), xytext=(-4, -12), textcoords="offset points",
                    ha="right", fontsize=7, color=TEXT_SECONDARY)
        for conj, cs in curves.items():
            cv = cs[side]
            ax.plot(cv["dijo"], cv["real"], color=SERIES[conj], linewidth=2, marker="o", markersize=5,
                    markeredgecolor=SURFACE, markeredgewidth=1.5, label=MODEL_NAMES[conj], zorder=3)
            ax.annotate(MODEL_NAMES[conj], xy=(cv["dijo"].iloc[-1], cv["real"].iloc[-1]), xytext=(4, 0),
                        textcoords="offset points", fontsize=7, color=TEXT_PRIMARY, va="center")
        ax.set_xlim(lo, hi + (hi - lo) * 0.25)
        ax.set_ylim(lo, hi)
        ax.set_title(f"{name}", loc="left", fontsize=10, color=TEXT_PRIMARY)
        ax.set_xlabel("Lo que dijo el modelo (probabilidad media de cada décimo de horas)", fontsize=8,
                      color=TEXT_SECONDARY)
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v * 100:.0f} %"))
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v * 100:.0f} %"))
    axes[0].set_ylabel("Tocaron el objetivo de verdad", fontsize=8, color=TEXT_SECONDARY)
    legend = axes[0].legend(loc="upper left", frameon=False, fontsize=8)
    for t in legend.get_texts():
        t.set_color(TEXT_PRIMARY)
    fig.suptitle(f"{pair}: ¿se cumple lo que dice el modelo? (desarrollo, fuera de muestra)", x=0.01, ha="left",
                 fontsize=11, color=TEXT_PRIMARY)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def plot_importance(pair: str, importance: pd.Series, path: Path, top: int = 12):
    imp = importance.head(top)[::-1]
    names = [TV_COLUMNS.get(k, BASE_NAMES.get(k, k)).split(" (")[0] for k in imp.index]
    tv = [k in TV_COLUMNS for k in imp.index]
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    ax.grid(axis="y", visible=False)
    colors = [SERIES["tradingview"] if t else SERIES["base"] for t in tv]
    ax.barh(range(len(imp)), imp.to_numpy(), color=colors, height=0.7, edgecolor=SURFACE, linewidth=2)
    ax.set_yticks(range(len(imp)))
    ax.set_yticklabels(names, fontsize=8, color=TEXT_PRIMARY)
    ax.set_xlabel("Cuánto mueve la probabilidad en promedio (log-odds)", fontsize=8, color=TEXT_SECONDARY)
    handles = [plt.Rectangle((0, 0), 1, 1, color=SERIES["tradingview"]), plt.Rectangle((0, 0), 1, 1, color=SERIES["base"])]
    legend = ax.legend(handles, ["Herramienta de TradingView", "Dato del modelo actual"], loc="lower right",
                       frameon=False, fontsize=8)
    for t in legend.get_texts():
        t.set_color(TEXT_PRIMARY)
    ax.set_title(f"{pair}: lo que más usa el modelo + TradingView", loc="left", fontsize=11, color=TEXT_PRIMARY)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------- informe

def _pct(v: float) -> str:
    return "-" if pd.isna(v) else f"{v * 100:.0f} %"


def _pts(v: float) -> str:
    return f"{v * 100:+.1f}"


def pair_section(pair: str, data: pd.DataFrame, rows: List[dict], chance: float, seps: Dict[str, List[dict]],
                 importance: pd.Series, figs: Dict[str, str], epochs: Tuple[pd.DataFrame, pd.DataFrame]) -> List[str]:
    a, b = epochs
    rep = repeated(rows)
    lines = [f"## {pair}", "",
             f"{len(data):,} horas de desarrollo: época 1 de {a.index[0]:%Y-%m} a {a.index[-1]:%Y-%m}, época 2 de "
             f"{b.index[0]:%Y-%m} a {b.index[-1]:%Y-%m}. Lo normal (cualquier hora): la compra toca el objetivo el "
             f"{_pct((data['res_long'] == 'tp').mean())} de las veces y la venta el {_pct((data['res_short'] == 'tp').mean())}.",
             "", "### 1. Patrones que se repiten en las dos épocas", "",
             f"**{len(rep)} patrones se repiten; por puro azar se repetirían ~{chance:.0f}** (mismo cálculo con el futuro "
             f"desordenado). " + ("Hay más de lo que da el azar: vale la pena mirarlos."
                                  if len(rep) > 1.5 * chance + 2 else
                                  "No es más de lo que da el azar: estos patrones no se distinguen de la casualidad."),
             ""]
    if rep:
        table = pd.DataFrame([{
            "herramienta": TV_COLUMNS[r["herramienta"]].split(" (")[0], "situación": r["situación"], "operación": r["lado"],
            "gana (época 1)": _pct(r["gana_1"]), "vs normal": _pts(r["ventaja_1"]),
            "gana (época 2)": _pct(r["gana_2"]), "vs normal ": _pts(r["ventaja_2"]),
            "horas": f"{r['horas_1']:,} / {r['horas_2']:,}"} for r in rep[:15]])
        lines += [table.to_markdown(index=False), "",
                  "«vs normal» = puntos de % por encima (+) o por debajo (−) de lo normal de esa época. Un patrón que "
                  "pierde en las dos épocas también sirve: dice cuándo NO entrar.", ""]

    lines += ["### 2. ¿Separa mejor las horas buenas de las malas con TradingView?", "",
              f"![calibración]({figs['calibracion']})", ""]
    sep_rows = []
    for conj, sep in seps.items():
        for s in sep:
            sep_rows.append({"modelo": MODEL_NAMES[conj], "operación": s["lado"], "todas las horas": _pct(s["todas"]),
                             "10 % que ve mejor": _pct(s["mejor_10"]), "10 % que ve peor": _pct(s["peor_10"]),
                             "diferencia": _pts(s["mejor_10"] - s["peor_10"]),
                             "operaría (≥ umbral)": f"{s['señales']:,} h · gana {_pct(s['gana_señales'])}"})
    lines += [pd.DataFrame(sep_rows).to_markdown(index=False), "",
              "Un modelo útil tiene una «diferencia» grande: el 10 % que ve mejor gana bastante más que el 10 % que ve "
              "peor, y en el gráfico su línea sube. Una línea plana es un modelo que no distingue.", "",
              "### 3. Lo que más usa el modelo + TradingView", "", f"![importancia]({figs['importancia']})", "",
              "Top 5: " + ", ".join(TV_COLUMNS.get(k, BASE_NAMES.get(k, k)).split(" (")[0] for k in importance.head(5).index)
              + ". Que el modelo use mucho un dato no prueba que sirva: puede usarlo para memorizar.", ""]
    return lines


def report_md(sections: List[str], cfg: dict, gran: str) -> str:
    r = cfg["riesgo"]
    head = [
        f"# Estudio de patrones con herramientas de TradingView · {gran} · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "",
        f"**Pregunta:** ¿qué tenían en común, en el momento de entrar, las operaciones que tocaron el objetivo? Cada hora "
        f"se plantea una compra y una venta con stop {r['sl_atr']} ATR y objetivo {r['tp_atr']} ATR (objetivo = 1.5 × stop), "
        f"48 velas como máximo, con spread y slippage del instrumento.",
        "",
        f"**Solo el periodo de desarrollo** (antes de {cfg['validacion']['inicio_prueba_final']}). Aquí se buscan ideas; "
        "nada de esto se usa para operar hasta comprobarlo aparte con reglas escritas antes (pre-registro) y datos que "
        "no se miraron.",
        "",
        "**Cómo no engañarse:** con 17 herramientas, varias situaciones cada una, compra y venta, se hacen cientos de "
        "comparaciones, y alguna sale bien por casualidad. Por eso: (1) un patrón debe repetirse en dos épocas "
        f"distintas, con ≥ {MIN_HOURS} horas en cada una y ≥ {MIN_EDGE * 100:.0f} puntos de diferencia; (2) se compara "
        "con cuántos patrones «se repiten» cuando se desordena el futuro (lo que da el azar); (3) horas seguidas "
        "comparten futuro, así que 1 000 horas valen bastante menos que 1 000 operaciones.",
        "",
    ]
    return "\n".join(head + sections)


def run_pair(cfg: dict, pair: str, gran: str, out_dir: Path, log=print) -> List[str]:
    from data_loader import load_from_dukascopy

    icfg = instrument_config(cfg, pair, gran)
    df = load_from_dukascopy(pair, gran, years=icfg["mercado"]["anios_historia"], refresh=False)
    data = study_data(df, icfg, pair)
    log(f"\n=== {pair} {gran}: {len(data):,} horas de desarrollo ===")
    a, b = split_epochs(data)
    rows = pattern_rows(a, b)
    log(f"    patrones que se repiten: {len(repeated(rows))}; calculando el control de azar...")
    chance = chance_count(a, b)

    seps, curves, importance = {}, {}, None
    threshold = float(icfg["modelo"]["umbral_probabilidad"])
    for conj in ("base", "tradingview"):
        proba, imp = ml_oos(df, icfg, pair, conj, log)
        seps[conj] = separation(proba, data, threshold)
        curves[conj] = {side: calibration_curve(proba, data, side) for side in SIDES}
        if conj == "tradingview":
            importance = imp
    figs = {"calibracion": f"calibracion_{pair}.png", "importancia": f"importancia_{pair}.png"}
    plot_calibration(pair, curves, out_dir / figs["calibracion"])
    plot_importance(pair, importance, out_dir / figs["importancia"])
    return pair_section(pair, data, rows, chance, seps, importance, figs, (a, b))


def main():
    cfg = load_trading_config()
    parser = argparse.ArgumentParser(description="Estudio de patrones con herramientas de TradingView (desarrollo)")
    parser.add_argument("--instrumentos", default=",".join(cfg.get("produccion", {}).get("activos") or
                                                           [cfg["mercado"]["par"]]))
    parser.add_argument("--temporalidad", default=cfg["mercado"]["temporalidad"])
    args = parser.parse_args()
    gran = args.temporalidad.upper()

    from report import REPORTS_DIR

    out_dir = REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}_patrones"
    out_dir.mkdir(parents=True, exist_ok=True)
    sections = []
    for pair in [p.strip().upper() for p in args.instrumentos.split(",") if p.strip()]:
        try:
            sections += run_pair(cfg, pair, gran, out_dir)
        except Exception as e:  # un instrumento sin datos no detiene a los demás
            print(f"[x] {pair}: {e}")
    md = report_md(sections, cfg, gran)
    (out_dir / "informe.md").write_text(md, encoding="utf-8")
    print("\n" + md + f"\n\n[v] Informe: {out_dir / 'informe.md'}")


if __name__ == "__main__":
    main()
