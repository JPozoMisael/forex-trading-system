"""
Correo de operación cerrada con el gráfico de cómo se dio: velas de antes,
durante y después, zonas de SL/TP (herramienta de posición), entrada,
salida, pips, dinero y duración, todo en hora local.

Si algo falla (BD, descarga de velas, gráfico) se envía el aviso en texto
de siempre: un problema del gráfico nunca deja al usuario sin el aviso.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd

from charts import (DOWN, GRID, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, UP, digits, display_name, duration_text,
                    move_text, plot_trade, trade_granularity)
from shared.config import GRANULARITY_MINUTES, settings
from shared.fx import quote_to_usd_rate
from shared.logger import get_logger
from shared.timefmt import fmt_local, zona

log = get_logger(__name__)

CONTEXT_BEFORE = 40  # velas antes de la entrada
CONTEXT_AFTER = 12   # velas después de la salida (si ya existen)
_SIDE = {"buy": "COMPRA", "sell": "VENTA"}
_REASONS = {"tp": "Take Profit", "sl": "Stop Loss", "manual": "cierre manual", "stop_out": "stop out"}


def load_order(order_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """Fila de la orden (SL, TP, riesgo, lotes). None si no hay id o la BD falla."""
    if order_id is None:
        return None
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(settings.postgres_dsn, connect_timeout=3)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"No se pudo leer la orden #{order_id}: {e}")
        return None


def trade_candles(pair: str, opened: datetime, closed: datetime, gran: str, now: datetime,
                  fetch: Optional[Callable] = None) -> pd.DataFrame:
    """Velas de `gran` alrededor de la operación (Dukascopy), solo cerradas."""
    from shared.dukascopy import fetch_mid_candles, only_closed

    fetch = fetch or fetch_mid_candles
    minutes = GRANULARITY_MINUTES[gran]
    start = opened - timedelta(minutes=minutes * CONTEXT_BEFORE * 1.6) - timedelta(days=3)
    end = min(now, closed + timedelta(minutes=minutes * CONTEXT_AFTER))
    df = fetch(pair, gran, start, end)
    if df is None or df.empty:
        return pd.DataFrame()
    df = only_closed(df, gran, now)[["open", "high", "low", "close"]].astype(float)
    before = df[df.index < pd.Timestamp(opened)].tail(CONTEXT_BEFORE)
    return pd.concat([before, df[df.index >= pd.Timestamp(opened)]])


def _ts(v) -> datetime:
    t = pd.Timestamp(v)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def _num(v) -> Optional[float]:
    return None if v is None or pd.isna(v) else float(v)


def trade_details(trade: Dict[str, Any], order: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Une el evento de cierre con la fila de la orden (SL/TP solo están en la BD)."""
    order = order or {}
    entry = float(trade["entry_price"])
    units = _num(trade.get("units")) or _num(order.get("units"))
    rate = quote_to_usd_rate(trade["pair"], entry)
    risk = _num(order.get("risk_amount"))
    pnl = _num(trade.get("pnl"))
    return {
        "pair": trade["pair"], "direction": str(trade["direction"]).lower(), "entry_price": entry,
        "exit_price": _num(trade.get("exit_price")), "stop_loss": _num(order.get("stop_loss")),
        "take_profit": _num(order.get("take_profit")), "opened_at": _ts(trade["opened_at"]),
        "closed_at": _ts(trade["closed_at"]), "duration_minutes": _num(trade.get("duration_minutes")),
        "pnl": pnl, "exit_reason": str(trade.get("exit_reason") or "").lower(),
        "money_per_unit": units * rate if units and rate else None,
        "r": pnl / risk if pnl is not None and risk else (_num(trade.get("roi_pct")) or 0) / 100 or None,
        "lots": _num(order.get("volume_lots")), "strategy": trade.get("strategy_name") or order.get("strategy_name"),
    }


def build_trade_email(t: Dict[str, Any], has_chart: bool) -> Tuple[str, str, str]:
    """(asunto, html, texto) de una operación cerrada, explicada en palabras."""
    pair, d = t["pair"], digits(t["pair"])
    buy = t["direction"] == "buy"
    side = _SIDE.get(t["direction"], t["direction"].upper())
    entry, exit_p, sl, tp, pnl = t["entry_price"], t["exit_price"], t["stop_loss"], t["take_profit"], t["pnl"]
    move = (exit_p - entry) * (1 if buy else -1) if exit_p is not None else 0.0
    won = (pnl if pnl is not None else move) >= 0
    reason = _REASONS.get(t["exit_reason"], t["exit_reason"] or "cierre")
    money = f"{'+' if won else '−'}${abs(pnl):,.2f}" if pnl is not None else "n/d"
    r_txt = f"{t['r']:+.2f} R" if t.get("r") is not None else "-"
    dur = duration_text(t["duration_minutes"]) if t.get("duration_minutes") is not None else "-"

    subject = (f"[{'Operación ganadora' if won else 'Operación cerrada con pérdida'}] {display_name(pair)} {side} · "
               f"{reason} · {money} ({move_text(pair, move, signed=True)})")
    esperaba = "subiera" if buy else "bajara"
    hizo = ("subió" if exit_p > entry else "bajó") if exit_p is not None else "se movió"
    story = (f"El modelo abrió una {side} de {display_name(pair)} el {fmt_local(t['opened_at'])} a {entry:.{d}f}, "
             f"esperando que el precio {esperaba}. El precio {hizo} y la operación se cerró por {reason} el "
             f"{fmt_local(t['closed_at'])} a {exit_p:.{d}f}" if exit_p is not None else "")
    story += f", {'ganando' if won else 'perdiendo'} {money.lstrip('+−')} ({move_text(pair, move)}). Duró {dur}."
    if t["exit_reason"] == "sl":
        story += (" Perder en el Stop Loss es lo previsto: se pierde solo lo que se decidió arriesgar (1 R, el 1 % "
                  "de la cuenta), nunca más.")
    elif t["exit_reason"] == "tp":
        story += " Llegó al objetivo: la ganancia es mayor que lo que se arriesgaba."
    story += " Una operación sola no dice si el modelo funciona: eso se ve con muchas, a lo largo de meses."

    def px(v):
        return f"{v:.{d}f}" if v is not None else "-"

    rows = [("Dirección", side), ("Entrada", f"{px(entry)} · {fmt_local(t['opened_at'])}"),
            ("Salida", f"{px(exit_p)} · {fmt_local(t['closed_at'])} ({reason})"),
            ("Stop Loss", f"{px(sl)} ({move_text(pair, sl - entry)} de la entrada)" if sl is not None else "-"),
            ("Take Profit", f"{px(tp)} ({move_text(pair, tp - entry)} de la entrada)" if tp is not None else "-"),
            ("Resultado", f"{money} · {move_text(pair, move, signed=True)} · {r_txt}"),
            ("Duración", dur), ("Tamaño", f"{t['lots']:.2f} lotes" if t.get("lots") else "-")]
    if sl is not None and tp is not None and sl != entry:
        rows.insert(5, ("Riesgo / Beneficio", f"1 : {abs(tp - entry) / abs(entry - sl):.2f}"))

    footer = f"Horas: {zona()}. Modo simulación (DRY_RUN): no se arriesga dinero real."
    text = "\n".join([subject, "", story, ""] + [f"{k}: {v}" for k, v in rows] + ["", footer])
    color = UP if won else DOWN
    trs = "".join(f"<tr><td style='padding:4px 10px;color:{TEXT_SECONDARY};border-bottom:1px solid {GRID}'>{k}</td>"
                  f"<td style='padding:4px 10px;border-bottom:1px solid {GRID}'>{v}</td></tr>" for k, v in rows)
    html = f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:{TEXT_PRIMARY};background:{SURFACE};max-width:900px">
<h1 style="font-size:19px;margin:0 0 6px">{display_name(pair)} · {side} cerrada · <span style="color:{color}">{money}</span></h1>
<p style="font-size:13px;margin:0 0 10px">{story}</p>
{'<img src="cid:operacion" alt="Gráfico de la operación" style="width:100%;max-width:900px">' if has_chart else ''}
<table style="border-collapse:collapse;font-size:13px;margin-top:8px">{trs}</table>
<p style="font-size:12px;color:{TEXT_SECONDARY}">{footer}</p>
</body></html>"""
    return subject, html, text


def send_trade_report(email, trade: Dict[str, Any], now: Optional[datetime] = None,
                      load: Callable = load_order, fetch: Optional[Callable] = None) -> bool:
    """Envía el correo con gráfico; si no se puede armar, el aviso en texto de siempre."""
    if not settings.enable_email_trades:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        t = trade_details(trade, load(trade.get("order_id")))
        images = {}
        try:
            minutes = t["duration_minutes"] or (t["closed_at"] - t["opened_at"]).total_seconds() / 60
            gran = trade_granularity(minutes)
            candles = trade_candles(t["pair"], t["opened_at"], t["closed_at"], gran, now, fetch=fetch)
            if len(candles) >= 5:
                images["operacion"] = plot_trade(t["pair"], candles, gran, t)
            else:
                log.warning(f"Sin velas para dibujar la operación de {t['pair']}; se envía sin gráfico.")
        except Exception:
            log.exception("No se pudo dibujar la operación; se envía sin gráfico")
        subject, html, text = build_trade_email(t, has_chart=bool(images))
        return email.send_html(subject, html, text, images=images)
    except Exception:
        log.exception("No se pudo armar el correo de la operación; se envía el aviso en texto")
        email.send_trade_closed_alert(trade)
        return False
