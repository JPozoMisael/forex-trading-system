"""
Informe de la validación walk-forward: gráfico de equity (PNG), tablas en
markdown, operaciones en CSV y la respuesta explícita a "¿el modelo supera a
las líneas base?". Se guarda en reports/<fecha>_<PAR>_<TF>/ (sin versionar).
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from validation import BUY_AND_HOLD, NO_TRADE, ValidationReport

REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"

# Paleta categórica validada (skill dataviz, modo claro): slots 1-3 pasan los
# controles de daltonismo entre todos los pares. "Sin operar" es una referencia
# (gris discontinuo), no una serie.
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
REFERENCE = "#8a8984"
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]


def _series_order(report: ValidationReport) -> List[str]:
    """Estrategias primero (en el orden pedido) y buy & hold al final: el color sigue a la entidad."""
    strategies = [n for n in report.results if n not in (NO_TRADE, BUY_AND_HOLD)]
    return strategies + [BUY_AND_HOLD]


def plot_equity(report: ValidationReport, path: Path, display_names: Dict[str, str]) -> Path:
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    capital = report.results[NO_TRADE].equity.iloc[0]
    ax.axhline(capital, color=REFERENCE, linewidth=1.2, linestyle=(0, (4, 3)), zorder=1)
    ax.annotate(display_names.get(NO_TRADE, NO_TRADE), xy=(1.0, capital), xycoords=("axes fraction", "data"),
                xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=TEXT_SECONDARY)

    for w in report.windows[1:]:
        ax.axvline(w.test_start, color=GRID, linewidth=1, zorder=0)

    names = _series_order(report)
    for i, name in enumerate(names[: len(SERIES_COLORS)]):
        eq = report.results[name].equity
        if eq.empty:
            continue
        color = SERIES_COLORS[i]
        label = display_names.get(name, name)
        ax.plot(eq.index, eq.values, color=color, linewidth=2, label=label, zorder=3, solid_capstyle="round")
        final = eq.iloc[-1]
        ret = (final / capital - 1) * 100
        ax.annotate(f"{label} {ret:+.1f}%", xy=(1.0, final), xycoords=("axes fraction", "data"),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=TEXT_PRIMARY)

    ax.set_title(f"Equity fuera de muestra · {report.pair} {report.granularity} · walk-forward de "
                 f"{len(report.windows)} ventanas, con costos", loc="left", fontsize=12, color=TEXT_PRIMARY, pad=12)
    ax.set_ylabel("Capital (USD)", color=TEXT_SECONDARY, fontsize=10)
    ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
    legend = ax.legend(loc="upper left", frameon=False, fontsize=9)
    for text in legend.get_texts():
        text.set_color(TEXT_PRIMARY)
    fig.text(0.01, 0.01, "Las líneas verticales separan las ventanas de prueba; en cada una el modelo se reentrena solo con el pasado.",
             fontsize=8, color=TEXT_SECONDARY)

    fig.tight_layout(rect=(0, 0.03, 0.88, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def _fmt(v, pattern="{:.2f}"):
    return "–" if v is None or (isinstance(v, float) and pd.isna(v)) else pattern.format(v)


def beats_baselines(report: ValidationReport, name: str) -> Dict[str, bool]:
    """¿El retorno neto fuera de muestra de `name` supera al de cada línea base?"""
    ret = report.results[name].metrics.total_return_pct
    others = [n for n in report.results if n != name]
    return {other: ret > report.results[other].metrics.total_return_pct for other in others}


def build_markdown(report: ValidationReport, chart_name: str, display_names: Dict[str, str],
                   main_strategy: Optional[str]) -> str:
    dn = lambda n: display_names.get(n, n)
    lines = [
        f"# Validación walk-forward · {report.pair} {report.granularity}",
        "",
        f"Generado: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
        f"{len(report.windows)} ventanas de prueba, de {report.windows[0].test_start:%Y-%m-%d} a "
        f"{report.windows[-1].test_end:%Y-%m-%d} · costos: spread {report.costs['spread_pips']} pips, "
        f"slippage {report.costs['slippage_pips']} pips por ejecución, comisión ${report.costs['commission_per_lot']}/lote.",
        "",
    ]

    if main_strategy and main_strategy in report.verdicts:
        v = report.verdicts[main_strategy]
        wins = beats_baselines(report, main_strategy)
        beats_all = all(wins.values())
        lines += [
            "## ¿El modelo supera a las líneas base?",
            "",
            f"**{'SÍ' if beats_all and v.label == 'VENTAJA CON EVIDENCIA' else 'NO'}.** "
            f"Veredicto estadístico: **{v.label}**. {v.explanation}",
            "",
        ]
        for other, won in wins.items():
            lines.append(f"- Contra {dn(other)}: {'gana' if won else 'pierde'} "
                         f"({report.results[main_strategy].metrics.total_return_pct:+.2f}% vs "
                         f"{report.results[other].metrics.total_return_pct:+.2f}%).")
        lines.append("")

    lines += [f"![Equity fuera de muestra]({chart_name})", "", "## Métricas totales fuera de muestra", "",
              "| Estrategia | Retorno neto | Max drawdown | Sharpe | Profit factor | Win rate | Operaciones |",
              "|---|---|---|---|---|---|---|"]
    for name, res in report.results.items():
        m = res.metrics
        lines.append(f"| {dn(name)} | {m.total_return_pct:+.2f}% | {m.max_drawdown_pct:.2f}% | {m.sharpe_ratio:.2f} | "
                     f"{_fmt(m.profit_factor) if m.total_trades else '–'} | "
                     f"{_fmt(m.win_rate_pct, '{:.1f}%') if m.total_trades else '–'} | {m.total_trades} |")

    lines += ["", "## Por ventana", ""]
    for name, res in report.results.items():
        if name == NO_TRADE:
            continue
        lines += [f"### {dn(name)}", "",
                  "| Ventana | Prueba | Retorno | Max DD | Sharpe | Profit factor | Win rate | Ops |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in res.per_window:
            lines.append(f"| {r['ventana']} | {r['desde']:%Y-%m-%d} → {r['hasta']:%Y-%m-%d} | {r['retorno_pct']:+.2f}% | "
                         f"{r['max_dd_pct']:.2f}% | {r['sharpe']:.2f} | "
                         f"{_fmt(r['profit_factor']) if r['operaciones'] else '–'} | "
                         f"{_fmt(r['win_rate_pct'], '{:.1f}%') if r['operaciones'] else '–'} | {r['operaciones']} |")
        lines.append("")

    for name, res in report.results.items():
        if not res.diagnostics:
            continue
        lines += [f"## Calidad del clasificador ({dn(name)})", "",
                  "Precisión = % de señales que tocaron el TP antes que el SL; el modelo aporta si supera a la tasa base. "
                  "Accuracy trivial = acierto de predecir siempre la clase mayoritaria.", "",
                  "| Ventana | Lado | Tasa base | Señales | Precisión | Accuracy | Accuracy trivial |",
                  "|---|---|---|---|---|---|---|"]
        for n, d in sorted(res.diagnostics.items()):
            for side, label in (("long", "Compra"), ("short", "Venta")):
                lines.append(f"| {n} | {label} | {_fmt(d[f'tasa_base_{side}'], '{:.3f}')} | {d[f'senales_{side}']} | "
                             f"{_fmt(d[f'precision_{side}'], '{:.3f}')} | {_fmt(d[f'accuracy_{side}'], '{:.3f}')} | "
                             f"{_fmt(d[f'accuracy_trivial_{side}'], '{:.3f}')} |")
        lines.append("")

    lines += ["## Veredicto estadístico", "",
              "Test de permutación contra la operación opuesta y bootstrap de la expectativa (en R), como en el script de Colab.", "",
              "| Estrategia | Veredicto | Ops | Expectativa (R) | IC 95% | p-valor |", "|---|---|---|---|---|---|"]
    for name, v in report.verdicts.items():
        lines.append(f"| {dn(name)} | {v.label} | {v.n_trades} | {_fmt(v.expectancy_r, '{:+.3f}')} | "
                     f"[{_fmt(v.ci_low, '{:+.3f}')}, {_fmt(v.ci_high, '{:+.3f}')}] | {_fmt(v.p_value, '{:.4f}')} |")
    lines.append("")
    return "\n".join(lines)


def write_report(report: ValidationReport, display_names: Optional[Dict[str, str]] = None,
                 main_strategy: Optional[str] = None, out_dir: Optional[Path] = None) -> Path:
    display_names = display_names or {}
    out_dir = out_dir or REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}_{report.pair}_{report.granularity}"
    out_dir.mkdir(parents=True, exist_ok=True)

    plot_equity(report, out_dir / "equity.png", display_names)
    (out_dir / "informe.md").write_text(build_markdown(report, "equity.png", display_names, main_strategy), encoding="utf-8")
    for name, res in report.results.items():
        if name in (NO_TRADE, BUY_AND_HOLD) or res.trades.empty:
            continue
        res.trades.to_csv(out_dir / f"operaciones_{name}.csv", index=False)
    return out_dir
