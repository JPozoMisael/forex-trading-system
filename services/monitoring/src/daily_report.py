"""
Reporte diario por email (lunes a viernes, DAILY_REPORT_HOUR_UTC).

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

def should_send(now: datetime, last_sent: Optional[str], hour_utc: int) -> bool:
    """Lunes a viernes, una vez al día, a partir de la hora indicada (recupera si el servicio estuvo caído)."""
    return now.weekday() < 5 and now.hour >= hour_utc and last_sent != now.date().isoformat()


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
        ax.annotate(f"Fib {ratio * 100:.1f}%  {price:.5f}", xy=(1.0, price), xycoords=("axes fraction", "data"),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=7.5, color=TEXT_SECONDARY)

    # Posición abierta: entrada, SL y TP
    for _, o in data.open_orders.iterrows():
        for price, color, label, style in ((o.get("entry_price"), TEXT_PRIMARY, "Entrada", (0, (5, 3))),
                                           (o.get("stop_loss"), DOWN, "SL", "-"),
                                           (o.get("take_profit"), UP, "TP", "-")):
            if price is None or pd.isna(price):
                continue
            ax.axhline(price, color=color, linewidth=1.4, linestyle=style, zorder=5)
            ax.annotate(f"{label} {float(price):.5f}", xy=(0.0, float(price)), xycoords=("axes fraction", "data"),
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
    ax.set_title(f"{data.pair} {data.granularity} · últimos {len(df)} periodos · cierre {close:.5f} · "
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


def build_report(data: ReportData) -> Tuple[str, str, str]:
    """(asunto, html, texto plano). El HTML referencia el gráfico como cid:grafico."""
    ok = data.health.get("overall_status", "UP") == "UP"
    model_short, model_detail = _model_view(data.strategy_state)
    since = data.now - timedelta(hours=24)
    closed = data.closed_orders
    last24 = closed[pd.to_datetime(closed["closed_at"], utc=True) >= since] if not closed.empty else closed
    stats = trade_stats(closed, data.initial_balance)
    close = float(data.candles["close"].iloc[-1])
    sessions = active_sessions(data.now)

    subject = (f"[Reporte diario] {data.pair} {data.now:%d/%m} · {'✅ sistema OK' if ok else '⚠️ revisar sistema'} · "
               f"{model_short} · {len(data.open_orders)} posición(es) abierta(s)")

    # Estado
    names = {"postgres": "Base de datos", "redis": "Redis", "broker": "Cuenta / execution-engine",
             "datos": "Velas (data-collector)", "estrategia": "Modelo (strategy-engine)"}
    status_icon = {"healthy": "✅", "unhealthy": "❌", "n/a": "➖"}
    health_rows = [(names[k], status_icon.get(v.get("status"), "?"), v.get("detail") or v.get("error") or "")
                   for k, v in data.health.items() if k in names]

    acc = data.account or {}
    account_line = (f"Balance {acc.get('balance', 0):,.2f} · Equity {acc.get('equity', 0):,.2f} {acc.get('currency', 'USD')}"
                    if acc else "sin datos de la cuenta")

    def order_row(o, closed_row=False):
        base = [str(o["direction"]).upper(), f"{float(o['entry_price']):.5f}" if o.get("entry_price") is not None else "-"]
        if closed_row:
            r = o.get("r_multiple")
            return base + [f"{float(o['exit_price']):.5f}" if o.get("exit_price") is not None else "-",
                           str(o.get("exit_reason") or "").upper(), _fmt_money(float(o.get("pnl") or 0)),
                           f"{float(r):+.2f} R" if r is not None and not pd.isna(r) else "-"]
        return base + [f"{float(o['stop_loss']):.5f}", f"{float(o['take_profit']):.5f}",
                       str(o.get("opened_at"))[:16]]

    open_rows = [order_row(o) for _, o in data.open_orders.iterrows()]
    closed_rows = [order_row(o, True) for _, o in last24.iterrows()]

    if stats["operaciones"]:
        pf = "∞" if stats["profit_factor"] == float("inf") else f"{stats['profit_factor']:.2f}"
        exp = f"{stats['expectativa_r']:+.2f} R" if stats.get("expectativa_r") is not None else "-"
        stats_rows = [("Operaciones", f"{stats['operaciones']} ({stats['ganadas']} ganadas, {stats['win_rate']:.0f} %)"),
                      ("Resultado", f"{_fmt_money(stats['pnl'])} ({stats['retorno_pct']:+.2f} %)"),
                      ("Profit factor", pf), ("Expectativa", exp), ("Máx. drawdown", f"{stats['max_dd_pct']:.1f} %")]
    else:
        stats_rows = [("Operaciones", "ninguna todavía")]
    ref = (f"Referencia del backtest (prueba final): ~{BACKTEST_REF['operaciones_mes']} operaciones/mes, "
           f"{BACKTEST_REF['expectativa_r']:+.2f} R por operación, profit factor {BACKTEST_REF['profit_factor']}.")

    # Texto plano
    lines = [subject, "",
             f"SISTEMA: {'todo OK' if ok else 'HAY COMPONENTES CON FALLO'}"]
    lines += [f"  {icon} {name}: {detail}" for name, icon, detail in health_rows]
    lines += ["", f"MODELO: {model_detail}",
              f"Precio actual {close:.5f}. Sesiones activas ahora: {', '.join(sessions) or 'ninguna (Asia)'}.",
              "", f"CUENTA SIMULADA: {account_line}"]
    lines += [f"  Abierta: {' | '.join(r)}" for r in open_rows] or ["  Sin posiciones abiertas."]
    lines += ["", "ÚLTIMAS 24 H:"] + ([f"  {' | '.join(r)}" for r in closed_rows] or ["  Ninguna operación cerrada."])
    lines += ["", "ACUMULADO:"] + [f"  {k}: {v}" for k, v in stats_rows] + [f"  {ref}"]
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
    html = f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:{TEXT_PRIMARY};background:{SURFACE};max-width:900px">
<h1 style="font-size:19px;margin:0 0 4px">Reporte diario · {data.pair} {data.granularity}</h1>
<div style="color:{TEXT_SECONDARY};font-size:13px">{data.now:%d/%m/%Y %H:%M} UTC ({(data.now - timedelta(hours=5)):%H:%M} en Ecuador) · modo simulación (DRY_RUN)</div>

<h2 {h2}>{'✅ Sistema funcionando' if ok else '⚠️ Hay componentes con fallo'}</h2>
{table(["Componente", "", "Detalle"], health_rows)}

<h2 {h2}>Qué ve el modelo</h2>
<p style="font-size:13px;margin:0">{model_detail}<br>Precio actual <b>{close:.5f}</b>. Sesiones activas ahora:
{', '.join(sessions) or 'ninguna (sesión de Asia)'}.</p>

<h2 {h2}>Gráfico</h2>
<img src="cid:grafico" alt="Gráfico de {data.pair}" style="width:100%;max-width:900px">

<h2 {h2}>Cuenta simulada</h2>
<p style="font-size:13px;margin:0 0 8px">{account_line}</p>
{table(["Dirección", "Entrada", "SL", "TP", "Abierta (UTC)"], open_rows) if open_rows else "<p style='font-size:13px'>Sin posiciones abiertas.</p>"}

<h2 {h2}>Operaciones cerradas en las últimas 24 h</h2>
{table(["Dirección", "Entrada", "Salida", "Motivo", "Resultado", "R"], closed_rows) if closed_rows else "<p style='font-size:13px'>Ninguna.</p>"}

<h2 {h2}>Acumulado desde el inicio</h2>
{table(["", ""], stats_rows)}
<p style="font-size:12px;color:{TEXT_SECONDARY}">{ref} El modelo no tiene ventaja probada: este periodo sirve para comprobarlo.</p>
</body></html>"""
    return subject, html, text


# ---------------------------------------------------------------- datos y envío

def collect(bus, checker, now: Optional[datetime] = None) -> ReportData:
    import psycopg2
    import psycopg2.extras

    now = now or datetime.now(timezone.utc)
    pair, gran = settings.forex_pairs[0], settings.candle_granularity
    conn = psycopg2.connect(settings.postgres_dsn, connect_timeout=5)
    try:
        candles = pd.read_sql(
            "SELECT timestamp, open, high, low, close FROM candles WHERE pair = %s AND granularity = %s AND complete "
            "ORDER BY timestamp DESC LIMIT 600", conn, params=(pair, gran))
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders WHERE pair = %s AND status = 'filled' ORDER BY opened_at", (pair,))
            open_orders = pd.DataFrame(cur.fetchall())
            cur.execute("SELECT * FROM orders WHERE pair = %s AND status = 'closed' ORDER BY closed_at", (pair,))
            closed = pd.DataFrame(cur.fetchall())
    finally:
        conn.close()
    if candles.empty:
        raise RuntimeError(f"No hay velas de {pair} {gran} en la BD")
    candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
    candles = candles.set_index("timestamp").sort_index().astype(float)

    return ReportData(
        pair=pair, granularity=gran, now=now, candles=candles,
        health=checker.run_full_check(),
        strategy_state=bus.get_json(f"{settings.key_strategy_state_prefix}{pair}"),
        account=bus.get_json(settings.key_account_snapshot),
        open_orders=open_orders, closed_orders=closed, initial_balance=settings.sim_initial_balance,
    )


def send_daily_report(email, telegram, bus, checker, now: Optional[datetime] = None) -> bool:
    data = collect(bus, checker, now)
    subject, html, text = build_report(data)
    sent = email.send_html(subject, html, text, images={"grafico": plot_chart(data)})
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
            data = collect(bus, checker)
            subject, html, _ = build_report(data)
            out = Path(args.guardar)
            out.mkdir(parents=True, exist_ok=True)
            (out / "grafico.png").write_bytes(plot_chart(data))
            (out / "reporte.html").write_text(html.replace("cid:grafico", "grafico.png"), encoding="utf-8")
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
