"""
Reportes por email antes de cada sesión (lunes a viernes, REPORT_TIMES en hora local;
por defecto 01:30 y 08:00 en Ecuador). Todas las horas se muestran en hora local.

Contenido: estado del sistema, qué ve el modelo en la última vela, cuenta
simulada, operaciones de las últimas 24 h, acumulado desde el inicio y un
gráfico de las últimas velas con EMAs, retrocesos de Fibonacci, sesiones de
Londres y Nueva York y la posición abierta (SL/TP).

Fibonacci, EMAs y sesiones son contexto visual para leer el gráfico: el
modelo NO los usa para decidir.

    python services/monitoring/src/daily_report.py --guardar reports/diario   # genera HTML + PNG, no envía
    python services/monitoring/src/daily_report.py --enviar                   # envía ahora por email
"""
import argparse
import io
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from charts import (DOWN, DPI, EMA_COLORS, GRID, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, TIMEFRAMES, UP,
                    bar_index as _bar_index, digits as _digits, display_name, draw_fibonacci, draw_position, fetch_recent,
                    fibonacci_levels, move_text, plot_timeframes, price_tag, timeframe_summary)

from shared.config import GRANULARITY_MINUTES, settings
from shared.logger import get_logger
from shared.timefmt import fmt_fecha, fmt_hora, fmt_local, local_tz, to_local, zona

log = get_logger(__name__)

# Horario aproximado de cada sesión en UTC (sin ajuste de horario de verano)
SESSIONS = {"Londres": (7, 16), "Nueva York": (12, 21)}
CHART_BARS = 120  # 5 días de H1

# Referencia de la prueba final (2023-10 a 2026-10, 76 operaciones en 36 meses)
def backtest_refs() -> Dict[str, Dict[str, float]]:
    """Resultado de la prueba final por instrumento (config.yaml, produccion.referencia_backtest)."""
    try:
        from shared.config import load_trading_config

        return (load_trading_config().get("produccion") or {}).get("referencia_backtest") or {}
    except Exception:
        return {}

@dataclass
class ReportData:
    pair: str
    granularity: str
    now: datetime
    candles: pd.DataFrame                       # open/high/low/close, índice UTC
    health: Dict[str, Any] = field(default_factory=dict)
    strategy_state: Optional[Dict[str, Any]] = None
    account: Optional[Dict[str, Any]] = None
    open_orders: pd.DataFrame = field(default_factory=pd.DataFrame)
    closed_orders: pd.DataFrame = field(default_factory=pd.DataFrame)  # todas, por closed_at
    initial_balance: float = 10000.0
    timeframes: Dict[str, pd.DataFrame] = field(default_factory=dict)  # otras temporalidades (contexto)


# ---------------------------------------------------------------- cálculos

CATCH_UP = timedelta(hours=2)  # si el servicio estuvo caído a la hora, se envía si no pasaron más de 2 h


def due_slot(now: datetime, last_sent: Optional[str], times_local: List[str]) -> Optional[str]:
    """
    Turno de reporte pendiente ("2026-10-27 01:30", hora local) o None. Lunes a
    viernes (hora local), una vez por horario. Un turno vencido hace más de 2 h
    ya no se envía: el reporte de Londres no sirve a mediodía.
    """
    local = to_local(now)
    if local.weekday() >= 5:
        return None
    for hhmm in sorted(times_local, reverse=True):
        h, m = (int(x) for x in hhmm.split(":"))
        start = local.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= local < start + CATCH_UP:
            slot = f"{local.date().isoformat()} {h:02d}:{m:02d}"
            return slot if slot != last_sent else None
    return None


def sessions_local_text() -> str:
    """'Londres abre 2:00 a. m. y cierra 11:00 a. m.; Nueva York ...' en hora local."""
    base = datetime(2026, 1, 5, tzinfo=timezone.utc)  # un lunes cualquiera: solo importa la hora
    return "; ".join(f"{name} abre {fmt_hora(base.replace(hour=a))} y cierra {fmt_hora(base.replace(hour=b))}"
                     for name, (a, b) in SESSIONS.items())


def upcoming_sessions(now: datetime) -> List[str]:
    """Sesiones que abren en las próximas 2 horas o abrieron hace menos de 2 (para titular el reporte)."""
    return [name for name, (a, _) in SESSIONS.items() if abs(a * 60 - (now.hour * 60 + now.minute)) <= 120]


def active_sessions(now: datetime) -> List[str]:
    return [name for name, (a, b) in SESSIONS.items() if a <= now.hour < b]


def trade_stats(closed: pd.DataFrame, initial_balance: float) -> Dict[str, Any]:
    if closed.empty:
        return {"operaciones": 0}
    pnl = closed["pnl"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    equity = initial_balance + pnl.cumsum()
    peak = pd.concat([pd.Series([initial_balance]), equity]).cummax().iloc[1:].to_numpy()
    dd = float(((peak - equity.to_numpy()) / peak).max() * 100)
    r = closed["r_multiple"].dropna().astype(float) if "r_multiple" in closed else pd.Series(dtype=float)
    return {
        "operaciones": len(closed),
        "ganadas": len(wins),
        "win_rate": len(wins) / len(closed) * 100,
        "pnl": float(pnl.sum()),
        "retorno_pct": float(pnl.sum() / initial_balance * 100),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "expectativa_r": float(r.mean()) if len(r) else None,
        "max_dd_pct": dd,
    }


# ---------------------------------------------------------------- gráfico

def plot_chart(data: ReportData) -> bytes:
    full = data.candles
    emas = {p: full["close"].ewm(span=p, adjust=False).mean() for p in EMA_COLORS}
    df = full.iloc[-CHART_BARS:]
    x = range(len(df))
    fib = fibonacci_levels(df)
    dg = _digits(data.pair)

    fig, (ax, ax_s) = plt.subplots(2, 1, figsize=(11, 6.2), dpi=DPI, sharex=True,
                                   gridspec_kw={"height_ratios": [12, 1.4], "hspace": 0.05})
    fig.patch.set_facecolor(SURFACE)
    for a in (ax, ax_s):
        a.set_facecolor(SURFACE)

    # Velas
    for i, (_, r) in zip(x, df.iterrows()):
        color = UP if r["close"] >= r["open"] else DOWN
        ax.vlines(i, r["low"], r["high"], color=color, linewidth=0.9, zorder=2)
        body_lo, body_hi = sorted((r["open"], r["close"]))
        ax.add_patch(plt.Rectangle((i - 0.35, body_lo), 0.7, max(body_hi - body_lo, 1e-6),
                                   facecolor=color, edgecolor=color, linewidth=0.6, zorder=3))

    # EMAs (calculadas con toda la historia cargada para que converjan)
    for p, color in EMA_COLORS.items():
        ax.plot(list(x), emas[p].iloc[-CHART_BARS:].to_numpy(), color=color, linewidth=1.6,
                label=f"EMA {p}", zorder=4)

    draw_fibonacci(ax, df, data.pair)

    # Posición abierta: herramienta "Posición larga/corta" desde la vela de entrada hasta ahora
    for _, o in data.open_orders.iterrows():
        entry = o.get("entry_price")
        if entry is None or pd.isna(entry):
            continue
        i_in = _bar_index(df.index, o.get("opened_at")) if o.get("opened_at") is not None else None
        sl, tp = (float(o[k]) if o.get(k) is not None and not pd.isna(o.get(k)) else None
                  for k in ("stop_loss", "take_profit"))
        draw_position(ax, (i_in if i_in is not None else len(df) - 6) - 0.5, len(df) - 0.5, data.pair,
                      str(o["direction"]).lower() == "buy", float(entry), sl, tp, _money_per_unit(o, data.pair, entry))

    # Operaciones cerradas dentro de la ventana
    for _, t in data.closed_orders.iterrows():
        if pd.isna(t.get("opened_at")) or pd.isna(t.get("closed_at")):
            continue
        i_in, i_out = _bar_index(df.index, t["opened_at"]), _bar_index(df.index, t["closed_at"])
        buy = str(t["direction"]) == "buy"
        if i_in is not None:
            ax.scatter([i_in], [t["entry_price"]], marker="^" if buy else "v", s=70, color=TEXT_PRIMARY,
                       edgecolors=SURFACE, linewidths=1.5, zorder=7)
        if i_out is not None and t.get("exit_price") is not None:
            ax.scatter([i_out], [t["exit_price"]], marker="X", s=60, color=TEXT_PRIMARY,
                       edgecolors=SURFACE, linewidths=1.5, zorder=7)

    # Franja de sesiones (Londres arriba, Nueva York abajo)
    hours = df.index.hour
    for row, (name, (a, b)) in enumerate(SESSIONS.items()):
        y = 1 - row
        active = [(a <= h < b) for h in hours]
        for i, on in zip(x, active):
            if on:
                ax_s.add_patch(plt.Rectangle((i - 0.5, y - 0.4), 1.0, 0.8, facecolor=GRID, edgecolor="none"))
    ax_s.set_ylim(-0.6, 1.6)
    ax_s.set_yticks([1, 0])
    ax_s.set_yticklabels(list(SESSIONS), fontsize=8, color=TEXT_SECONDARY)

    # Ejes
    # Días marcados a la medianoche LOCAL (hora del usuario)
    local_index = df.index.tz_convert(local_tz())
    ticks = [i for i, ts in zip(x, local_index) if ts.hour == 0]
    ax_s.set_xticks(ticks)
    dias = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
    ax_s.set_xticklabels([f"{dias[local_index[i].weekday()]} {local_index[i].day}" for i in ticks],
                         fontsize=8, color=TEXT_SECONDARY)
    ax.set_xlim(-1, len(df))
    # El eje incluye SL y TP de la posición abierta aunque estén fuera del rango de las velas
    prices = [float(df["low"].min()), float(df["high"].max())]
    for col in ("stop_loss", "take_profit"):
        if col in data.open_orders:
            prices += [float(v) for v in data.open_orders[col].dropna()]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.05
    ax.set_ylim(lo - pad, hi + pad)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    for a in (ax, ax_s):
        for side in ("top", "right"):
            a.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            a.spines[side].set_color(GRID)
        a.tick_params(colors=TEXT_SECONDARY, labelsize=8)
    ax_s.tick_params(axis="y", length=0)

    price_tag(ax, df, data.pair)
    close = float(df["close"].iloc[-1])
    ax.set_title(f"{display_name(data.pair)} {data.granularity} · últimos {len(df)} periodos · cierre {close:.{dg}f} · "
                 f"Fibonacci del movimiento {fib['tendencia']}", loc="left", fontsize=11, color=TEXT_PRIMARY, pad=10)
    legend = ax.legend(loc="upper left", frameon=False, fontsize=8)
    for t in legend.get_texts():
        t.set_color(TEXT_PRIMARY)
    fig.text(0.01, 0.005, f"Días y horas en {zona()}. {sessions_local_text()}\n"
             "Fibonacci, EMAs y sesiones son contexto visual: el modelo no los usa. ▲/▼ entrada de una operación "
             "cerrada, X salida.", fontsize=7.5, color=TEXT_SECONDARY)
    fig.subplots_adjust(left=0.08, right=0.84, top=0.93, bottom=0.11)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------- texto

def _money_per_unit(o, pair: str, price) -> Optional[float]:
    """USD por cada unidad de precio que se mueve la posición (para mostrar dinero en el gráfico)."""
    from shared.fx import quote_to_usd_rate

    units = o.get("units")
    rate = quote_to_usd_rate(pair, float(price))
    if units is None or pd.isna(units) or rate is None:
        return None
    return float(units) * rate


def _fmt_money(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


_SIDE = {"buy": "COMPRA", "sell": "VENTA"}


def _model_view(state: Optional[Dict[str, Any]], gran_minutes: int = 60) -> Tuple[str, str]:
    """(resumen corto, explicación en palabras) de lo que vio el modelo en la última vela, en hora local."""
    if not state:
        return "sin evaluaciones todavía", "El modelo todavía no ha analizado ninguna vela."
    start = to_local(state["vela"])
    end = start + timedelta(minutes=gran_minutes)
    text = f"Última vela analizada: de {fmt_hora(start)} a {fmt_hora(end)} del {fmt_fecha(start)}."
    short = "sin señal"
    for s in (state.get("estrategias") or {}).values():
        if s.get("error"):
            return "ERROR", text + " El modelo tuvo un ERROR al analizarla (revisar logs)."
        if "p_long" in s:
            umbral = s["umbral"] * 100
            text += (f" Probabilidad de que una COMPRA llegue a su objetivo: {s['p_long'] * 100:.0f} %; "
                     f"una VENTA: {s['p_short'] * 100:.0f} %. El modelo solo opera si alguna llega al {umbral:.0f} %.")
        if s.get("senal"):
            side = _SIDE.get(str(s["senal"]).lower(), str(s["senal"]).upper())
            short = f"SEÑAL {side}"
            text += f" → Señal de {side}."
        elif "p_long" in s:
            text += " → Sin señal: ninguna llega, así que no hace nada."
    if not any("p_long" in s or s.get("error") for s in (state.get("estrategias") or {}).values()):
        text += (" Este instrumento NO pasó la prueba del modelo: se muestra el gráfico para que lo sigas, "
                 "pero el sistema no opera aquí.")
        short = "no opera"
    return short, text


def _ref_text(pair: str, refs: Dict[str, Dict[str, float]]) -> str:
    r = refs.get(pair.upper())
    if not r:
        return ""
    return (f"Lo que dio la prueba (oct 2023 - oct 2026): ~{r['operaciones_mes']} operaciones al mes, "
            f"{r['retorno_pct']:+.1f} % en 3 años (comprar y mantener: {r['buy_hold_pct']:+.1f} %).")


def _position_text(o, pair: str, price: float, digits: int) -> str:
    """Una posición abierta explicada en palabras: qué pasa si toca el SL o el TP y cómo va ahora."""
    from shared.fx import quote_to_usd_rate

    side = _SIDE.get(str(o["direction"]).lower(), str(o["direction"]).upper())
    entry, sl, tp = (float(o[k]) if o.get(k) is not None and not pd.isna(o.get(k)) else None
                     for k in ("entry_price", "stop_loss", "take_profit"))
    text = f"{side} abierta el {fmt_local(o.get('opened_at'))} a {entry:.{digits}f}."
    units = o.get("units")
    rate = quote_to_usd_rate(pair, price)
    if entry is None or sl is None or tp is None or units is None or pd.isna(units) or rate is None:
        return text
    buy = str(o["direction"]).lower() == "buy"
    loss = abs(entry - sl) * float(units) * rate
    gain = abs(tp - entry) * float(units) * rate
    now_pnl = ((price - entry) if buy else (entry - price)) * float(units) * rate
    hacia_sl, hacia_tp = ("baja", "sube") if buy else ("sube", "baja")
    return (f"{text} Si el precio {hacia_sl} a {sl:.{digits}f} (Stop Loss) se cierra perdiendo ~${loss:,.0f}; "
            f"si {hacia_tp} a {tp:.{digits}f} (Take Profit) se cierra ganando ~${gain:,.0f}. "
            f"Ahora va {_fmt_money(now_pnl)}. No tienes que hacer nada: el sistema la cierra solo.")


def _instrument_section(data: ReportData, refs: Optional[Dict] = None) -> Dict[str, Any]:
    """Lo de un instrumento: qué ve el modelo, posiciones y operaciones de 24 h."""
    d = _digits(data.pair)
    gran_minutes = GRANULARITY_MINUTES.get(data.granularity.upper(), 60)
    model_short, model_detail = _model_view(data.strategy_state, gran_minutes)
    closed = data.closed_orders
    since = data.now - timedelta(hours=24)
    last24 = closed[pd.to_datetime(closed["closed_at"], utc=True) >= since] if not closed.empty else closed
    price = float(data.candles["close"].iloc[-1])

    def px(v):
        return f"{float(v):.{d}f}" if v is not None and not pd.isna(v) else "-"

    open_rows = [[_SIDE.get(str(o["direction"]).lower(), str(o["direction"]).upper()), px(o.get("entry_price")),
                  px(o.get("stop_loss")), px(o.get("take_profit")), fmt_local(o.get("opened_at"))]
                 for _, o in data.open_orders.iterrows()]
    open_texts = [_position_text(o, data.pair, price, d) for _, o in data.open_orders.iterrows()]
    reasons = {"tp": "Take Profit (ganancia)", "sl": "Stop Loss (pérdida)"}
    closed_rows = []
    for _, o in last24.iterrows():
        r = o.get("r_multiple")
        closed_rows.append([_SIDE.get(str(o["direction"]).lower(), str(o["direction"]).upper()),
                            px(o.get("entry_price")), px(o.get("exit_price")), fmt_local(o.get("closed_at")),
                            reasons.get(str(o.get("exit_reason") or "").lower(), str(o.get("exit_reason") or "")),
                            _fmt_money(float(o.get("pnl") or 0)),
                            f"{float(r):+.2f} R" if r is not None and not pd.isna(r) else "-"])
    if model_short.startswith("SEÑAL") and open_rows:
        model_detail += " Como ya hay una posición abierta en este instrumento, no se abre otra."
    ref = _ref_text(data.pair, refs if refs is not None else backtest_refs())
    return {"pair": data.pair, "name": display_name(data.pair), "short": model_short, "detail": model_detail, "ref": ref,
            "close": px(price), "tf_rows": timeframe_rows(data), "has_tf_chart": bool(data.timeframes),
            "open_rows": open_rows, "open_texts": open_texts, "closed_rows": closed_rows}


def timeframe_rows(data: ReportData) -> List[List[str]]:
    """Una fila por temporalidad: tendencia, vela típica, rango y el Fibonacci más cercano al precio."""
    d = _digits(data.pair)
    frames = {data.granularity.upper(): data.candles, **data.timeframes}
    order = ["D", "H4", data.granularity.upper(), "M30", "M15"]
    names = {**{g: n for g, (n, _) in TIMEFRAMES.items()}, "H1": "1 hora"}
    rows = []
    for gran in dict.fromkeys(order):
        df = frames.get(gran)
        if df is None or len(df) < 30:
            continue
        bars = TIMEFRAMES.get(gran, (None, CHART_BARS))[1]
        s = timeframe_summary(df, data.pair, bars)
        where = "justo en" if abs(s["fib_dist"]) < s["atr"] * 0.1 else             f"{move_text(data.pair, s['fib_dist'])} {'encima' if s['fib_dist'] > 0 else 'debajo'} de"
        label = names.get(gran, gran) + (" (la del modelo)" if gran == data.granularity.upper() else "")
        rows.append([label, s["tendencia"], move_text(data.pair, s["atr"]), move_text(data.pair, s["rango"]),
                     f"{where} Fib {s['fib_ratio'] * 100:.1f} % ({s['fib_precio']:.{d}f})"])
    return rows


def build_report(datas) -> Tuple[str, str, str]:
    """
    (asunto, html, texto plano) para uno o varios instrumentos. El HTML
    referencia cada gráfico como cid:grafico_<PAR>. Estado del sistema, cuenta
    y acumulado salen del primero (son comunes a todos).
    """
    datas = [datas] if isinstance(datas, ReportData) else list(datas)
    first = datas[0]
    ok = first.health.get("overall_status", "UP") == "UP"
    refs = backtest_refs()
    sections = [_instrument_section(d, refs) for d in datas]
    closed_parts = [d.closed_orders for d in datas if not d.closed_orders.empty]
    closed_all = pd.concat(closed_parts, ignore_index=True).sort_values("closed_at") if closed_parts else pd.DataFrame()
    stats = trade_stats(closed_all, first.initial_balance)
    sessions = active_sessions(first.now)
    n_open = sum(len(d.open_orders) for d in datas)

    upcoming = upcoming_sessions(first.now)
    title = f"Sesión de {' y '.join(upcoming)}" if upcoming else "Reporte"
    signals = [f"{s['name']} {s['short']}" for s in sections if s["short"].startswith("SEÑAL")]
    subject = (f"[{title}] {fmt_local(first.now)} · {'✅ sistema OK' if ok else '⚠️ revisar sistema'} · "
               f"{', '.join(signals) if signals else 'sin señales'} · {n_open} posición(es) abierta(s)")
    when = f"Reporte del {fmt_local(first.now)} ({zona()}). Todas las horas de este correo están en {zona()}."
    sessions_now = (f"Sesiones abiertas ahora: {', '.join(sessions) or 'ninguna (sesión de Asia)'}. "
                    f"{sessions_local_text()} ({zona()}).")

    names = {"postgres": "Base de datos", "redis": "Redis", "broker": "Cuenta / execution-engine",
             "datos": "Velas (data-collector)", "estrategia": "Modelo (strategy-engine)"}
    status_icon = {"healthy": "✅", "unhealthy": "❌", "n/a": "➖"}
    health_rows = [(names[k], status_icon.get(v.get("status"), "?"), v.get("detail") or v.get("error") or "")
                   for k, v in first.health.items() if k in names]

    acc = first.account or {}
    account_line = (f"Balance {acc.get('balance', 0):,.2f} · Equity {acc.get('equity', 0):,.2f} {acc.get('currency', 'USD')}"
                    if acc else "sin datos de la cuenta")

    if stats["operaciones"]:
        pf = "∞" if stats["profit_factor"] == float("inf") else f"{stats['profit_factor']:.2f}"
        exp = f"{stats['expectativa_r']:+.2f} R" if stats.get("expectativa_r") is not None else "-"
        stats_rows = [("Operaciones", f"{stats['operaciones']} ({stats['ganadas']} ganadas, {stats['win_rate']:.0f} %)"),
                      ("Resultado", f"{_fmt_money(stats['pnl'])} ({stats['retorno_pct']:+.2f} %)"),
                      ("Profit factor", pf), ("Expectativa", exp), ("Máx. drawdown", f"{stats['max_dd_pct']:.1f} %")]
    else:
        stats_rows = [("Operaciones", "ninguna todavía")]
    ref = ("Cada instrumento muestra lo que dio su prueba con datos de 2023 a 2026; estas semanas sirven para "
           "comprobar si en vivo se parece.")

    # Texto plano
    lines = [subject, "", when, "", f"SISTEMA: {'todo OK' if ok else 'HAY COMPONENTES CON FALLO'}"]
    lines += [f"  {icon} {name}: {detail}" for name, icon, detail in health_rows]
    lines += ["", sessions_now]
    for s in sections:
        lines += ["", f"== {s['name']} · precio {s['close']} ==", f"MODELO: {s['detail']}"] + ([f"  {s['ref']}"] if s["ref"] else [])
        lines += [f"  {r[0]}: tendencia {r[1]}, vela típica {r[2]}, rango {r[3]}, precio {r[4]}" for r in s["tf_rows"]]
        lines += [f"  POSICIÓN: {t}" for t in s["open_texts"]] or ["  Sin posiciones abiertas."]
        lines += [f"  Cerrada (24 h): {' | '.join(r)}" for r in s["closed_rows"]]
    lines += ["", f"CUENTA SIMULADA: {account_line}", "", "ACUMULADO (todos los instrumentos):"]
    lines += [f"  {k}: {v}" for k, v in stats_rows] + [f"  {ref}"]
    lines += ["", "Modo simulación (DRY_RUN): no se envían órdenes a ningún broker."]
    text = "\n".join(lines)

    # HTML
    def table(headers, rows):
        th = "".join(f"<th style='text-align:left;padding:4px 10px;color:{TEXT_SECONDARY};font-weight:600;"
                     f"border-bottom:1px solid {GRID}'>{h}</th>" for h in headers)
        trs = "".join("<tr>" + "".join(f"<td style='padding:4px 10px;border-bottom:1px solid {GRID}'>{c}</td>"
                                       for c in r) + "</tr>" for r in rows)
        return f"<table style='border-collapse:collapse;font-size:13px'><tr>{th}</tr>{trs}</table>"

    h2 = f"style='font-size:15px;margin:22px 0 8px;color:{TEXT_PRIMARY}'"
    p = "style='font-size:13px;margin:0 0 8px'"
    parts = []
    for s in sections:
        open_html = ("".join(f"<p {p}><b>Posición abierta:</b> {t}</p>" for t in s["open_texts"])
                     + table(["Dirección", "Entrada", "Stop Loss", "Take Profit", "Abierta"], s["open_rows"])
                     if s["open_rows"] else f"<p {p}>Sin posiciones abiertas.</p>")
        closed_html = (f"<p {p}><b>Cerradas en las últimas 24 h</b></p>"
                       + table(["Dirección", "Entrada", "Salida", "Cerrada", "Motivo", "Resultado", "R"], s["closed_rows"])
                       if s["closed_rows"] else "")
        tf_html = ""
        if s["tf_rows"]:
            tf_html = (f"<p {p}><b>Todas las temporalidades</b> (vela típica = cuánto se mueve una vela en promedio; "
                       f"rango = del mínimo al máximo de lo que muestra cada gráfico)</p>"
                       + table(["Temporalidad", "Tendencia", "Vela típica", "Rango", "Precio frente a Fibonacci"],
                               s["tf_rows"])
                       + (f"<img src=\"cid:tf_{s['pair']}\" alt=\"{s['name']} en varias temporalidades\" "
                          f"style=\"width:100%;max-width:900px;margin-top:8px\">" if s["has_tf_chart"] else ""))
        parts.append(f"""
<h2 {h2}>{s['name']} · precio {s['close']}</h2>
<p {p}>{s['detail']}</p>
{f"<p style='font-size:12px;color:{TEXT_SECONDARY};margin:0 0 8px'>{s['ref']}</p>" if s['ref'] else ""}
<img src="cid:grafico_{s['pair']}" alt="Gráfico de {s['name']}" style="width:100%;max-width:900px">
{open_html}
{closed_html}
{tf_html}""")

    html = f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:{TEXT_PRIMARY};background:{SURFACE};max-width:900px">
<h1 style="font-size:19px;margin:0 0 4px">{title} · {', '.join(display_name(d.pair) for d in datas)} · velas de {first.granularity}</h1>
<div style="color:{TEXT_SECONDARY};font-size:13px">{when} Modo simulación (DRY_RUN): no se arriesga dinero real.<br>{sessions_now}</div>

<h2 {h2}>{'✅ Sistema funcionando' if ok else '⚠️ Hay componentes con fallo'}</h2>
{table(["Componente", "", "Detalle"], health_rows)}
{''.join(parts)}

<h2 {h2}>Cuenta simulada</h2>
<p {p}>{account_line}</p>

<h2 {h2}>Acumulado desde el inicio (todos los instrumentos)</h2>
{table(["", ""], stats_rows)}
<p style="font-size:12px;color:{TEXT_SECONDARY}">{ref} El modelo no tiene ventaja probada: este periodo sirve para comprobarlo.</p>
</body></html>"""
    return subject, html, text


# ---------------------------------------------------------------- datos y envío

def _load_pair(conn, pair: str, gran: str):
    import psycopg2.extras

    candles = pd.read_sql(
        "SELECT timestamp, open, high, low, close FROM candles WHERE pair = %s AND granularity = %s AND complete "
        "ORDER BY timestamp DESC LIMIT 600", conn, params=(pair, gran))
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM orders WHERE pair = %s AND status = 'filled' ORDER BY opened_at", (pair,))
        open_orders = pd.DataFrame(cur.fetchall())
        cur.execute("SELECT * FROM orders WHERE pair = %s AND status = 'closed' ORDER BY closed_at", (pair,))
        closed = pd.DataFrame(cur.fetchall())
    if not candles.empty:
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
        candles = candles.set_index("timestamp").sort_index().astype(float)
    return candles, open_orders, closed


def collect_timeframes(pair: str, now: datetime, skip: str = "", fetch=None) -> Dict[str, pd.DataFrame]:
    """Velas de las otras temporalidades (Dukascopy). Si una falla se omite: el reporte sale igual."""
    frames = {}
    for gran, (_, bars) in TIMEFRAMES.items():
        if gran == skip.upper():
            continue
        try:
            df = fetch_recent(pair, gran, bars, now, fetch=fetch)
        except Exception as e:
            log.warning(f"Reporte: no se pudieron bajar velas {gran} de {pair}: {e}")
            continue
        if len(df) >= 30:
            frames[gran] = df
    return frames


def report_images(datas: List[ReportData]) -> Dict[str, bytes]:
    """Gráfico principal y cuadrícula de temporalidades de cada instrumento (la cuadrícula es opcional)."""
    images = {}
    for d in datas:
        images[f"grafico_{d.pair}"] = plot_chart(d)
        if d.timeframes:
            try:
                images[f"tf_{d.pair}"] = plot_timeframes(d.pair, d.timeframes)
            except Exception:
                log.exception(f"No se pudo dibujar las temporalidades de {d.pair}")
    return images


def collect(bus, checker, now: Optional[datetime] = None) -> List[ReportData]:
    """Un ReportData por cada par de FOREX_PAIRS que tenga velas en la BD."""
    import psycopg2

    now = now or datetime.now(timezone.utc)
    gran = settings.candle_granularity
    health = checker.run_full_check()
    account = bus.get_json(settings.key_account_snapshot)
    conn = psycopg2.connect(settings.postgres_dsn, connect_timeout=5)
    out = []
    try:
        for pair in settings.forex_pairs:
            candles, open_orders, closed = _load_pair(conn, pair, gran)
            if candles.empty:
                log.warning(f"Reporte: no hay velas de {pair} {gran} en la BD; se omite.")
                continue
            out.append(ReportData(
                pair=pair, granularity=gran, now=now, candles=candles, health=health,
                strategy_state=bus.get_json(f"{settings.key_strategy_state_prefix}{pair}"), account=account,
                open_orders=open_orders, closed_orders=closed, initial_balance=settings.sim_initial_balance,
                timeframes=collect_timeframes(pair, now, skip=gran),
            ))
    finally:
        conn.close()
    if not out:
        raise RuntimeError(f"No hay velas de ningún par ({settings.forex_pairs}) en la BD")
    return out


def send_daily_report(email, telegram, bus, checker, now: Optional[datetime] = None) -> bool:
    datas = collect(bus, checker, now)
    subject, html, text = build_report(datas)
    images = report_images(datas)
    sent = email.send_html(subject, html, text, images=images)
    telegram.send_message(text.replace("_", "/"))  # "_" rompe el Markdown de Telegram
    return sent


def main():
    from email_alerts import EmailNotifier
    from health_check import HealthChecker
    from shared.bus import EventBus
    from telegram_alerts import TelegramNotifier

    parser = argparse.ArgumentParser(description="Reporte diario del sistema")
    parser.add_argument("--guardar", metavar="CARPETA", help="escribe reporte.html y grafico.png sin enviar")
    parser.add_argument("--enviar", action="store_true", help="envía el reporte ahora por email (y Telegram)")
    args = parser.parse_args()

    bus, checker = EventBus(), HealthChecker()
    try:
        if args.guardar:
            datas = collect(bus, checker)
            subject, html, _ = build_report(datas)
            out = Path(args.guardar)
            out.mkdir(parents=True, exist_ok=True)
            for name, png in report_images(datas).items():
                (out / f"{name}.png").write_bytes(png)
                html = html.replace(f"cid:{name}", f"{name}.png")
            (out / "reporte.html").write_text(html, encoding="utf-8")
            print(f"[v] {subject}\n    -> {out / 'reporte.html'}")
        elif args.enviar:
            ok = send_daily_report(EmailNotifier(), TelegramNotifier(), bus, checker)
            print("[v] Reporte enviado" if ok else "[x] No se pudo enviar el email (ver log)")
        else:
            parser.print_help()
    finally:
        checker.close()
        bus.close()


if __name__ == "__main__":
    main()
