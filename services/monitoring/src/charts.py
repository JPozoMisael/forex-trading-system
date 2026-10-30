"""
Gráficos estilo TradingView para los correos (reporte y operación cerrada).

- Velas, EMA 50/200, retrocesos de Fibonacci y etiqueta del precio actual.
- Herramienta "Posición larga/corta": zona verde hasta el Take Profit y zona
  naranja hasta el Stop Loss, con pips (o USD/oz en el oro), dinero y la
  relación riesgo/beneficio.
- "Rango de precios" y "Rango de fechas": cuánto se movió el precio y cuánto
  duró la operación.
- Varias temporalidades (diario, 4 h, 30 min, 15 min) para ver el contexto.

Todo es contexto visual: el modelo decide solo con velas de H1 y sus propias
variables, no con estas herramientas. Horas en la zona del usuario.
"""
import io
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from shared.config import GRANULARITY_MINUTES
from shared.fx import get_pip_size
from shared.timefmt import fmt_hora, local_tz, zona

# Colores (skill dataviz, modo claro). Alcista/bajista = slots validados verde y naranja.
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
REFERENCE = "#8a8984"
UP = "#1baf7a"
DOWN = "#eb6834"
EMA_COLORS = {50: "#2a78d6", 200: TEXT_SECONDARY}
FIB_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)

DPI = 170  # nitidez en pantallas de alta densidad (celular)
EMA_WARMUP = 200  # velas extra para que la EMA 200 ya esté estable en la ventana

# Temporalidades del reporte: nombre para el usuario y cuántas velas se muestran
TIMEFRAMES = {"D": ("Diario", 90), "H4": ("4 horas", 90), "M30": ("30 minutos", 96), "M15": ("15 minutos", 96)}

_DIAS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


# ---------------------------------------------------------------- formato

def display_name(pair: str) -> str:
    """'EUR/USD', 'Oro (XAU/USD)'."""
    p = pair.upper().replace("_", "/")
    return f"Oro ({p})" if p.startswith("XAU") else p


def digits(pair: str) -> int:
    """Decimales con que se cotiza: oro 2, yen 3, resto 5."""
    p = pair.upper()
    return 2 if p.startswith("XAU") else 3 if "JPY" in p else 5


def move_text(pair: str, diff: float, signed: bool = False) -> str:
    """Un movimiento de precio como lo lee un trader: pips en divisas, dólares por onza en el oro."""
    sign = ("+" if diff >= 0 else "−") if signed else ""
    if pair.upper().startswith("XAU"):
        return f"{sign}{abs(diff):,.2f} USD/oz"
    return f"{sign}{abs(diff) / get_pip_size(pair):,.1f} pips"


def duration_text(minutes: float) -> str:
    """'6 h 0 min', '2 d 3 h', '45 min'."""
    m = int(round(minutes))
    if m >= 1440:
        return f"{m // 1440} d {(m % 1440) // 60} h"
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


# ---------------------------------------------------------------- cálculos

def fibonacci_levels(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Retrocesos del último gran movimiento de la ventana: del mínimo al máximo
    (si el máximo es posterior, tendencia alcista) o al revés. 0 % es el
    extremo más reciente.
    """
    hi_t, lo_t = df["high"].idxmax(), df["low"].idxmin()
    hi, lo = float(df["high"].max()), float(df["low"].min())
    up = hi_t > lo_t
    rng = hi - lo
    levels = [(r, hi - r * rng) if up else (r, lo + r * rng) for r in FIB_RATIOS]
    return {"tendencia": "alcista" if up else "bajista", "alto": hi, "bajo": lo,
            "alto_t": hi_t, "bajo_t": lo_t, "niveles": levels}


def emas(df: pd.DataFrame) -> Dict[int, pd.Series]:
    return {p: df["close"].ewm(span=p, adjust=False).mean() for p in EMA_COLORS}


def timeframe_summary(df: pd.DataFrame, pair: str, bars: int, price: Optional[float] = None) -> Dict[str, Any]:
    """
    Lectura rápida de una temporalidad: tendencia por EMAs, movimiento típico
    de una vela (ATR 14), rango de la ventana y nivel de Fibonacci más cercano.
    `price`: precio actual. Sin él se usa el cierre de la última vela de `df`,
    que en el diario es el de ayer: las filas de una tabla deben compararse
    todas contra el mismo precio.
    """
    e = emas(df)
    close = float(price) if price is not None else float(df["close"].iloc[-1])
    e50, e200 = float(e[50].iloc[-1]), float(e[200].iloc[-1])
    if close > e50 > e200:
        trend = "alcista"
    elif close < e50 < e200:
        trend = "bajista"
    else:
        trend = "mixta"
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    window = df.iloc[-bars:]
    fib = fibonacci_levels(window)
    ratio, level = min(fib["niveles"], key=lambda rl: abs(rl[1] - close))
    return {"tendencia": trend, "cierre": close, "atr": float(tr.iloc[-14:].mean()),
            "rango": float(window["high"].max() - window["low"].min()),
            "fib_ratio": ratio, "fib_precio": level, "fib_dist": close - level}


def fetch_recent(pair: str, gran: str, bars: int, now: datetime, fetch: Optional[Callable] = None) -> pd.DataFrame:
    """Últimas `bars` velas cerradas (+ las de calentamiento de la EMA 200) desde Dukascopy."""
    from shared.dukascopy import fetch_mid_candles, only_closed

    fetch = fetch or fetch_mid_candles
    minutes = GRANULARITY_MINUTES[gran.upper()]
    total = bars + EMA_WARMUP
    # Margen para fines de semana y feriados
    start = now - timedelta(minutes=minutes * total * 1.6) - timedelta(days=4)
    df = fetch(pair, gran, start, now)
    if df is None or df.empty:
        return pd.DataFrame()
    return only_closed(df, gran, now).tail(total)[["open", "high", "low", "close"]].astype(float)


# ---------------------------------------------------------------- piezas de dibujo

def new_figure(rows: int = 1, cols: int = 1, size=(11, 6.2), **kw):
    fig, axes = plt.subplots(rows, cols, figsize=size, dpi=DPI, **kw)
    fig.patch.set_facecolor(SURFACE)
    for a in (axes.flat if hasattr(axes, "flat") else [axes]):
        a.set_facecolor(SURFACE)
    return fig, axes


def style_axes(ax):
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)


def draw_candles(ax, df: pd.DataFrame, width: float = 0.7):
    n = len(df)
    lw = 0.9 if n <= 130 else 0.6
    for i, (o, h, l, c) in enumerate(df[["open", "high", "low", "close"]].itertuples(index=False)):
        color = UP if c >= o else DOWN
        ax.vlines(i, l, h, color=color, linewidth=lw, zorder=2)
        lo, hi = sorted((o, c))
        ax.add_patch(plt.Rectangle((i - width / 2, lo), width, max(hi - lo, 1e-9),
                                   facecolor=color, edgecolor=color, linewidth=0.5, zorder=3))


def draw_emas(ax, full: pd.DataFrame, bars: int, legend: bool = True):
    for p, series in emas(full).items():
        ax.plot(range(min(bars, len(full))), series.iloc[-bars:].to_numpy(), color=EMA_COLORS[p],
                linewidth=1.5, label=f"EMA {p}", zorder=4)
    if legend:
        lg = ax.legend(loc="upper left", frameon=False, fontsize=8)
        for t in lg.get_texts():
            t.set_color(TEXT_PRIMARY)


def draw_fibonacci(ax, df: pd.DataFrame, pair: str, fontsize: float = 7.5):
    dg = digits(pair)
    fib = fibonacci_levels(df)
    close = float(df["close"].iloc[-1])
    span = fib["alto"] - fib["bajo"]
    for ratio, price in fib["niveles"]:
        ax.axhline(price, color=REFERENCE, linewidth=0.8, linestyle=(0, (2, 3)), zorder=1)
        if abs(price - close) < span * 0.035:
            continue  # la etiqueta del precio actual ocupa ese lugar
        ax.annotate(f"{ratio * 100:.1f}%  {price:.{dg}f}", xy=(1.0, price), xycoords=("axes fraction", "data"),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=fontsize,
                    color=TEXT_SECONDARY)
    return fib


def price_tag(ax, df: pd.DataFrame, pair: str, fontsize: float = 8):
    """Etiqueta del último precio sobre el eje derecho, como en TradingView."""
    last = df.iloc[-1]
    color = UP if last["close"] >= last["open"] else DOWN
    ax.axhline(last["close"], color=color, linewidth=0.8, linestyle=(0, (1, 2)), zorder=1)
    ax.annotate(f"{last['close']:.{digits(pair)}f}", xy=(1.0, last["close"]), xycoords=("axes fraction", "data"),
                xytext=(6, 0), textcoords="offset points", va="center", fontsize=fontsize, color="white",
                fontweight="bold", zorder=9,
                bbox=dict(boxstyle="square,pad=0.25", facecolor=color, edgecolor="none"))


def time_ticks(ax, index: pd.DatetimeIndex, gran: str, max_ticks: int = 7):
    """Marcas del eje X en hora local: días a medianoche o, si la ventana es corta, horas."""
    local = index.tz_convert(local_tz())
    minutes = GRANULARITY_MINUTES.get(gran.upper(), 60)
    if minutes >= 1440:
        ticks = [i for i in range(len(local)) if i == 0 or local[i].month != local[i - 1].month]
        labels = [f"{local[i]:%m/%Y}" for i in ticks]
    else:
        days = [i for i in range(1, len(local)) if local[i].date() != local[i - 1].date()]
        if 2 <= len(days) <= max_ticks:
            ticks = days
            labels = [f"{_DIAS[local[i].weekday()]} {local[i].day}" for i in ticks]
        else:
            step = max(1, len(local) // max_ticks)
            ticks = list(range(0, len(local), step))
            labels = [f"{_DIAS[local[i].weekday()]} {fmt_hora(local[i])}" for i in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=7.5, color=TEXT_SECONDARY)


def fit_ylim(ax, df: pd.DataFrame, extra: List[float] = (), bottom_room: float = 0.0):
    """Ajusta el eje Y a las velas y a `extra` (SL, TP...); `bottom_room` deja espacio libre abajo."""
    prices = [float(df["low"].min()), float(df["high"].max())] + [float(p) for p in extra if p is not None]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.06 or hi * 0.001
    ax.set_ylim(lo - pad - (hi - lo) * bottom_room, hi + pad)


def bar_index(index: pd.DatetimeIndex, ts) -> Optional[int]:
    """Vela que contiene `ts` (None si está fuera de la ventana)."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    step = index[-1] - index[-2] if len(index) > 1 else pd.Timedelta(hours=1)
    if ts < index[0] or ts > index[-1] + step:
        return None
    return int(min(index.searchsorted(ts, side="right") - 1, len(index) - 1))


def draw_position(ax, x0: float, x1: float, pair: str, buy: bool, entry: float, sl: Optional[float],
                  tp: Optional[float], money_per_unit: Optional[float] = None):
    """
    Herramienta "Posición larga/corta" de TradingView: zona de ganancia (hasta
    el TP) y de pérdida (hasta el SL) entre las velas x0 y x1, con pips y dinero.
    `money_per_unit` = USD que se ganan o pierden por cada unidad de precio movida.
    """
    dg = digits(pair)
    side = "COMPRA" if buy else "VENTA"
    width = max(x1 - x0, 1.0)
    # Zona angosta (posición recién abierta, pegada al borde derecho): etiquetas alineadas a su
    # final, hacia la izquierda, para que no se salgan del gráfico
    narrow = width < 30
    lx, ha = (x0 + width, "right") if narrow else (x0 + width / 2, "center")
    for level, color, name in ((tp, UP, "Objetivo (TP)"), (sl, DOWN, "Stop (SL)")):
        if level is None:
            continue
        lo, hi = sorted((entry, level))
        ax.add_patch(plt.Rectangle((x0, lo), width, hi - lo, facecolor=color, alpha=0.16, edgecolor="none", zorder=1))
        ax.hlines(level, x0, x0 + width, color=color, linewidth=1.3, zorder=5)
        money = f" · {'+' if color == UP else '−'}${abs(level - entry) * money_per_unit:,.0f}" if money_per_unit else ""
        ax.annotate(f"{name} {level:.{dg}f} · {move_text(pair, level - entry)}{money}",
                    xy=(lx, level), ha=ha, va="bottom" if (level > entry) else "top",
                    xytext=(0, 3 if level > entry else -3), textcoords="offset points", fontsize=8, color="white",
                    fontweight="bold", zorder=9, bbox=dict(boxstyle="round,pad=0.3", facecolor=color, edgecolor="none"))
    ax.hlines(entry, x0, x0 + width, color=TEXT_PRIMARY, linewidth=1.2, linestyles=(0, (5, 3)), zorder=5)
    rr = (f" · Riesgo/Beneficio 1 : {abs(tp - entry) / abs(entry - sl):.2f}"
          if sl is not None and tp is not None and sl != entry else "")
    ax.annotate(f"{side} {entry:.{dg}f}{rr}", xy=(x0 + width, entry) if narrow else (x0, entry),
                xytext=(-3 if narrow else 3, 3 if buy else -3), textcoords="offset points", ha="right" if narrow else "left",
                va="bottom" if buy else "top", fontsize=8, color=TEXT_PRIMARY, fontweight="bold", zorder=9,
                bbox=dict(boxstyle="round,pad=0.25", facecolor=SURFACE, edgecolor=GRID))


def to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------- gráficos completos

def plot_timeframes(pair: str, frames: Dict[str, pd.DataFrame]) -> bytes:
    """Cuadrícula 2x2 con las temporalidades disponibles (diario, 4 h, 30 min, 15 min)."""
    grans = [g for g in TIMEFRAMES if g in frames and not frames[g].empty][:4]
    if not grans:
        raise ValueError(f"Sin velas de ninguna temporalidad para {pair}")
    fig, axes = new_figure(2, 2, size=(11, 7.4))
    for ax, gran in zip(axes.flat, grans):
        name, bars = TIMEFRAMES[gran]
        full = frames[gran]
        df = full.iloc[-bars:]
        draw_candles(ax, df)
        draw_emas(ax, full, len(df), legend=False)
        draw_fibonacci(ax, df, pair, fontsize=6.5)
        price_tag(ax, df, pair, fontsize=7)
        s = timeframe_summary(full, pair, bars)
        ax.set_xlim(-1, len(df))
        fit_ylim(ax, df)
        time_ticks(ax, df.index, gran, max_ticks=5)
        style_axes(ax)
        ax.tick_params(labelsize=7)
        ax.set_title(f"{name} · tendencia {s['tendencia']} · vela típica {move_text(pair, s['atr'])}",
                     loc="left", fontsize=9.5, color=TEXT_PRIMARY, pad=6)
    for ax in list(axes.flat)[len(grans):]:
        ax.set_visible(False)
    fig.suptitle(f"{display_name(pair)} en varias temporalidades", x=0.01, ha="left", fontsize=12,
                 color=TEXT_PRIMARY)
    fig.text(0.01, 0.008, f"Horas: {zona()}. Líneas: EMA 50 (azul), EMA 200 (gris), Fibonacci (punteado) y "
             "último precio.\nTendencia alcista = precio sobre EMA 50 y EMA 50 sobre EMA 200 (bajista al revés; "
             "si no, mixta). Es contexto: el modelo decide solo con velas de 1 hora.", fontsize=7, color=TEXT_SECONDARY)
    fig.subplots_adjust(left=0.07, right=0.89, top=0.9, bottom=0.09, hspace=0.38, wspace=0.32)
    return to_png(fig)


def trade_granularity(duration_minutes: float) -> str:
    """Temporalidad para dibujar una operación con suficiente detalle (unas 8 a 70 velas)."""
    if duration_minutes <= 180:
        return "M5"
    if duration_minutes <= 1080:
        return "M15"
    if duration_minutes <= 4 * 1440:
        return "H1"
    return "H4"


def plot_trade(pair: str, candles: pd.DataFrame, gran: str, trade: Dict[str, Any]) -> bytes:
    """
    Cómo se dio una operación: velas antes, durante y después, la herramienta
    de posición (SL/TP), la entrada (▲/▼), la salida (X), el rango de precios
    recorrido y el rango de fechas que duró.

    `trade`: direction, entry_price, exit_price, opened_at, closed_at,
    stop_loss, take_profit (pueden faltar), pnl, money_per_unit (opcional).
    """
    df = candles
    dg = digits(pair)
    buy = str(trade["direction"]).lower() == "buy"
    entry, exit_p = float(trade["entry_price"]), trade.get("exit_price")
    sl, tp = trade.get("stop_loss"), trade.get("take_profit")
    i_in = bar_index(df.index, trade["opened_at"])
    i_in = 0 if i_in is None else i_in
    i_out = bar_index(df.index, trade["closed_at"]) if trade.get("closed_at") is not None else None
    end = i_out if i_out is not None else len(df) - 1

    fig, ax = new_figure(size=(11, 6.4))
    draw_candles(ax, df)
    draw_emas(ax, df, len(df))
    draw_position(ax, i_in - 0.5, end + 0.5, pair, buy, entry, sl, tp, trade.get("money_per_unit"))

    # Entrada y salida
    ax.scatter([i_in], [entry], marker="^" if buy else "v", s=110, color=TEXT_PRIMARY, edgecolors=SURFACE,
               linewidths=1.5, zorder=8)
    if exit_p is not None and i_out is not None:
        exit_p = float(exit_p)
        won = (exit_p - entry) * (1 if buy else -1) >= 0
        color = UP if won else DOWN
        ax.scatter([i_out], [exit_p], marker="X", s=110, color=color, edgecolors=SURFACE, linewidths=1.5, zorder=8)
        # Rango de precios: flecha de la entrada a la salida, a la derecha de la salida
        xr = min(i_out + 1.5, len(df) - 0.5)
        ax.annotate("", xy=(xr, exit_p), xytext=(xr, entry),
                    arrowprops=dict(arrowstyle="-|>", color=color, linewidth=1.6), zorder=8)
        pnl = trade.get("pnl")
        money = f"\n{'+' if (pnl or 0) >= 0 else '−'}${abs(pnl):,.2f}" if pnl is not None else ""
        ax.annotate(f"Salida {exit_p:.{dg}f}\n{move_text(pair, (exit_p - entry) * (1 if buy else -1), signed=True)}"
                    f"{money}", xy=(xr, (entry + exit_p) / 2), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=color, fontweight="bold", zorder=9,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=SURFACE, edgecolor=color))

    extra = [entry] + [float(v) for v in (sl, tp, exit_p) if v is not None]
    fit_ylim(ax, df, extra, bottom_room=0.1)
    # Rango de fechas: corchete en el espacio libre de abajo, con la duración
    y0, y1 = ax.get_ylim()
    yb = y0 + (y1 - y0) * 0.025
    minutes = trade.get("duration_minutes")
    if minutes is None and trade.get("closed_at") is not None:
        minutes = (pd.Timestamp(trade["closed_at"]) - pd.Timestamp(trade["opened_at"])).total_seconds() / 60
    ax.annotate("", xy=(end, yb), xytext=(i_in, yb),
                arrowprops=dict(arrowstyle="|-|", color=TEXT_SECONDARY, linewidth=1.1, mutation_scale=4), zorder=8)
    label = f"Duró {duration_text(minutes)} · {end - i_in + 1} velas" if minutes is not None else \
        f"Abierta hace {end - i_in + 1} velas"
    ax.annotate(label, xy=((i_in + end) / 2, yb), xytext=(0, 4), textcoords="offset points", ha="center",
                fontsize=8, color=TEXT_SECONDARY, zorder=9)

    ax.set_xlim(-1, len(df) + 4)
    time_ticks(ax, df.index, gran)
    style_axes(ax)
    price_tag(ax, df, pair)
    gname = {"M5": "5 minutos", "M15": "15 minutos", "H1": "1 hora", "H4": "4 horas"}.get(gran, gran)
    ax.set_title(f"{display_name(pair)} · {'COMPRA' if buy else 'VENTA'} · velas de {gname}",
                 loc="left", fontsize=11.5, color=TEXT_PRIMARY, pad=10)
    fig.text(0.01, 0.01, f"Horas: {zona()}. Zona verde: hasta el Take Profit (ganancia). Zona naranja: hasta el "
             "Stop Loss (pérdida). ▲/▼ entrada, X salida, flecha = recorrido del precio.",
             fontsize=7.5, color=TEXT_SECONDARY)
    fig.subplots_adjust(left=0.08, right=0.89, top=0.92, bottom=0.1)
    return to_png(fig)
