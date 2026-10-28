"""
Reportes por email antes de cada sesión (lunes a viernes, REPORT_TIMES_UTC;
por defecto 06:30 y 13:00 UTC = 1:30 y 8:00 en Ecuador).

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

from shared.config import settings
from shared.logger import get_logger

log = get_logger(__name__)

# Horario aproximado de cada sesión en UTC (sin ajuste de horario de verano)
SESSIONS = {"Londres": (7, 16), "Nueva York": (12, 21)}
FIB_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
CHART_BARS = 120  # 5 días de H1

# Referencia de la prueba final (2023-10 a 2026-10, 76 operaciones en 36 meses)
BACKTEST_REF = {"operaciones_mes": 2.1, "expectativa_r": 0.04, "profit_factor": 1.05}

# Colores (skill dataviz, modo claro). Alcista/bajista = slots validados verde y naranja.
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
REFERENCE = "#8a8984"
UP = "#1baf7a"
DOWN = "#eb6834"
EMA_COLORS = {50: "#2a78d6", 200: TEXT_SECONDARY}


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


# ---------------------------------------------------------------- cálculos

CATCH_UP = timedelta(hours=2)  # si el servicio estuvo caído a la hora, se envía si no pasaron más de 2 h


def due_slot(now: datetime, last_sent: Optional[str], times_utc: List[str]) -> Optional[str]:
    """
    Turno de reporte pendiente ("2026-10-27 06:30") o None. Lunes a viernes,
    una vez por horario. Un turno vencido hace más de 2 h ya no se envía: el
    reporte de Londres no sirve a mediodía.
    """
    if now.weekday() >= 5:
        return None
    for hhmm in sorted(times_utc, reverse=True):
        h, m = (int(x) for x in hhmm.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= now < start + CATCH_UP:
            slot = f"{now.date().isoformat()} {h:02d}:{m:02d}"
            return slot if slot != last_sent else None
    return None


def upcoming_sessions(now: datetime) -> List[str]:
    """Sesiones que abren en las próximas 2 horas o abrieron hace menos de 2 (para titular el reporte)."""
    return [name for name, (a, _) in SESSIONS.items() if abs(a * 60 - (now.hour * 60 + now.minute)) <= 120]


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

def _bar_index(index: pd.DatetimeIndex, ts) -> Optional[int]:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    if ts < index[0] or ts > index[-1] + (index[-1] - index[-2]):
        return None
    return int(min(index.searchsorted(ts, side="right") - 1, len(index) - 1))


def plot_chart(data: ReportData) -> bytes:
    full = data.candles
    emas = {p: full["close"].ewm(span=p, adjust=False).mean() for p in EMA_COLORS}
    df = full.iloc[-CHART_BARS:]
    x = range(len(df))
    fib = fibonacci_levels(df)
    dg = _digits(data.pair)

    fig, (ax, ax_s) = plt.subplots(2, 1, figsize=(11, 6.2), dpi=130, sharex=True,
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

    # Fibonacci
    for ratio, price in fib["niveles"]:
        ax.axhline(price, color=REFERENCE, linewidth=0.8, linestyle=(0, (2, 3)), zorder=1)
        ax.annotate(f"Fib {ratio * 100:.1f}%  {price:.{dg}f}", xy=(1.0, price), xycoords=("axes fraction", "data"),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=7.5, color=TEXT_SECONDARY)

    # Posición abierta: entrada, SL y TP
    for _, o in data.open_orders.iterrows():
        for price, color, label, style in ((o.get("entry_price"), TEXT_PRIMARY, "Entrada", (0, (5, 3))),
                                           (o.get("stop_loss"), DOWN, "SL", "-"),
                                           (o.get("take_profit"), UP, "TP", "-")):
            if price is None or pd.isna(price):
                continue
            ax.axhline(price, color=color, linewidth=1.4, linestyle=style, zorder=5)
            ax.annotate(f"{label} {float(price):.{dg}f}", xy=(0.0, float(price)), xycoords=("axes fraction", "data"),
                        xytext=(4, 3), textcoords="offset points", fontsize=8, color=TEXT_PRIMARY, zorder=6)

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
    ticks = [i for i, ts in zip(x, df.index) if ts.hour == 0]
    ax_s.set_xticks(ticks)
    dias = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
    ax_s.set_xticklabels([f"{dias[df.index[i].weekday()]} {df.index[i].day}" for i in ticks],
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

    close = float(df["close"].iloc[-1])
    ax.set_title(f"{data.pair} {data.granularity} · últimos {len(df)} periodos · cierre {close:.{dg}f} · "
                 f"Fibonacci del movimiento {fib['tendencia']}", loc="left", fontsize=11, color=TEXT_PRIMARY, pad=10)
    legend = ax.legend(loc="upper left", frameon=False, fontsize=8)
    for t in legend.get_texts():
        t.set_color(TEXT_PRIMARY)
    fig.text(0.01, 0.005, "Hora UTC (Ecuador = UTC-5). Fibonacci, EMAs y sesiones son contexto visual: el modelo no los usa. "
             "▲/▼ entrada de una operación cerrada, X salida.", fontsize=7.5, color=TEXT_SECONDARY)
    fig.subplots_adjust(left=0.07, right=0.84, top=0.93, bottom=0.08)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------- texto

def _fmt_money(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def _model_view(state: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """(resumen corto, detalle) de lo que vio el modelo en la última vela evaluada."""
    if not state:
        return "sin evaluaciones todavía", "El strategy-engine aún no ha evaluado ninguna vela."
    vela = str(state.get("vela", "?"))[:16].replace("T", " ")
    parts = []
    short = "sin señal"
    for name, s in (state.get("estrategias") or {}).items():
        if s.get("error"):
            parts.append(f"{name}: ERROR al evaluar")
            continue
        if "p_long" in s:
            parts.append(f"{name}: P(compra gana) {s['p_long'] * 100:.1f} % · P(venta gana) {s['p_short'] * 100:.1f} % "
                         f"· umbral {s['umbral'] * 100:.0f} %")
        if s.get("senal"):
            short = f"SEÑAL {str(s['senal']).upper()}"
            parts.append(f"{name}: señal {str(s['senal']).upper()}")
    return short, f"Vela {vela} UTC. " + ("; ".join(parts) if parts else "sin datos de probabilidad")


def _digits(pair: str) -> int:
    """Decimales con que se cotiza: oro 2, yen 3, resto 5."""
    p = pair.upper()
    return 2 if p.startswith("XAU") else 3 if "JPY" in p else 5


def _instrument_section(data: ReportData) -> Dict[str, Any]:
    """Lo de un instrumento: qué ve el modelo, posiciones y operaciones de 24 h."""
    d = _digits(data.pair)
    model_short, model_detail = _model_view(data.strategy_state)
    closed = data.closed_orders
    since = data.now - timedelta(hours=24)
    last24 = closed[pd.to_datetime(closed["closed_at"], utc=True) >= since] if not closed.empty else closed

    def px(v):
        return f"{float(v):.{d}f}" if v is not None and not pd.isna(v) else "-"

    open_rows = [[str(o["direction"]).upper(), px(o.get("entry_price")), px(o.get("stop_loss")),
                  px(o.get("take_profit")), str(o.get("opened_at"))[:16]] for _, o in data.open_orders.iterrows()]
    closed_rows = []
    for _, o in last24.iterrows():
        r = o.get("r_multiple")
        closed_rows.append([str(o["direction"]).upper(), px(o.get("entry_price")), px(o.get("exit_price")),
                            str(o.get("exit_reason") or "").upper(), _fmt_money(float(o.get("pnl") or 0)),
                            f"{float(r):+.2f} R" if r is not None and not pd.isna(r) else "-"])
    return {"pair": data.pair, "short": model_short, "detail": model_detail,
            "close": px(data.candles["close"].iloc[-1]), "open_rows": open_rows, "closed_rows": closed_rows}


def build_report(datas) -> Tuple[str, str, str]:
    """
    (asunto, html, texto plano) para uno o varios instrumentos. El HTML
    referencia cada gráfico como cid:grafico_<PAR>. Estado del sistema, cuenta
    y acumulado salen del primero (son comunes a todos).
    """
    datas = [datas] if isinstance(datas, ReportData) else list(datas)
    first = datas[0]
    ok = first.health.get("overall_status", "UP") == "UP"
    sections = [_instrument_section(d) for d in datas]
    closed_parts = [d.closed_orders for d in datas if not d.closed_orders.empty]
    closed_all = pd.concat(closed_parts, ignore_index=True).sort_values("closed_at") if closed_parts else pd.DataFrame()
    stats = trade_stats(closed_all, first.initial_balance)
    sessions = active_sessions(first.now)
    n_open = sum(len(d.open_orders) for d in datas)

    upcoming = upcoming_sessions(first.now)
    title = f"Sesión de {' y '.join(upcoming)}" if upcoming else "Reporte"
    signals = [f"{s['pair']} {s['short']}" for s in sections if s["short"].startswith("SEÑAL")]
    subject = (f"[{title}] {first.now:%d/%m} · {'✅ sistema OK' if ok else '⚠️ revisar sistema'} · "
               f"{', '.join(signals) if signals else 'sin señales'} · {n_open} posición(es) abierta(s)")

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
    ref = (f"Referencia del backtest de EUR_USD H1 (prueba final): ~{BACKTEST_REF['operaciones_mes']} operaciones/mes, "
           f"{BACKTEST_REF['expectativa_r']:+.2f} R por operación, profit factor {BACKTEST_REF['profit_factor']}.")

    # Texto plano
    lines = [subject, "", f"SISTEMA: {'todo OK' if ok else 'HAY COMPONENTES CON FALLO'}"]
    lines += [f"  {icon} {name}: {detail}" for name, icon, detail in health_rows]
    lines += ["", f"Sesiones activas ahora: {', '.join(sessions) or 'ninguna (Asia)'}."]
    for s in sections:
        lines += ["", f"== {s['pair']} · precio {s['close']} ==", f"MODELO: {s['detail']}"]
        lines += [f"  Abierta: {' | '.join(r)}" for r in s["open_rows"]] or ["  Sin posiciones abiertas."]
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
        open_html = (table(["Dirección", "Entrada", "SL", "TP", "Abierta (UTC)"], s["open_rows"])
                     if s["open_rows"] else f"<p {p}>Sin posiciones abiertas.</p>")
        closed_html = (f"<p {p}><b>Cerradas en las últimas 24 h</b></p>"
                       + table(["Dirección", "Entrada", "Salida", "Motivo", "Resultado", "R"], s["closed_rows"])
                       if s["closed_rows"] else "")
        parts.append(f"""
<h2 {h2}>{s['pair']} · precio {s['close']}</h2>
<p {p}>{s['detail']}</p>
<img src="cid:grafico_{s['pair']}" alt="Gráfico de {s['pair']}" style="width:100%;max-width:900px">
{open_html}
{closed_html}""")

    html = f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:{TEXT_PRIMARY};background:{SURFACE};max-width:900px">
<h1 style="font-size:19px;margin:0 0 4px">{title} · {', '.join(d.pair for d in datas)} · {first.granularity}</h1>
<div style="color:{TEXT_SECONDARY};font-size:13px">{first.now:%d/%m/%Y %H:%M} UTC ({(first.now - timedelta(hours=5)):%H:%M} en Ecuador) · modo simulación (DRY_RUN) · sesiones activas: {', '.join(sessions) or 'ninguna (Asia)'}</div>

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
            ))
    finally:
        conn.close()
    if not out:
        raise RuntimeError(f"No hay velas de ningún par ({settings.forex_pairs}) en la BD")
    return out


def send_daily_report(email, telegram, bus, checker, now: Optional[datetime] = None) -> bool:
    datas = collect(bus, checker, now)
    subject, html, text = build_report(datas)
    images = {f"grafico_{d.pair}": plot_chart(d) for d in datas}
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
            for d in datas:
                (out / f"grafico_{d.pair}.png").write_bytes(plot_chart(d))
                html = html.replace(f"cid:grafico_{d.pair}", f"grafico_{d.pair}.png")
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
