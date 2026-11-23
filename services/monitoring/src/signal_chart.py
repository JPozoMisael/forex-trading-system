"""
Correo de señal con gráfico explicado: pisos, techos, soportes y resistencias,
la anatomía de la última vela (cuerpo y mechas) y la operación (entrada, SL, TP).

Es una ayuda para aprender a leer el gráfico, NO lo que decide el modelo: el
modelo solo usa sus datos de las últimas velas (ver "POR QUÉ" del correo). Los
niveles se calculan con una regla simple y fija para que siempre se dibujen igual:

- Techo / piso de 24 h: máximo y mínimo de las últimas 24 velas.
- Resistencia / soporte: máximos y mínimos de giro (la vela más alta o más baja
  entre las PIVOT velas de cada lado) de las últimas LOOKBACK velas, agrupando
  los que están a menos de media vela típica (ATR). Cuantos más "toques", más
  gente mira ese precio.
"""
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
from typing import Any, Callable, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import pandas as pd

from charts import (DOWN, GRID, REFERENCE, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, UP, digits, display_name,
                    draw_candles, draw_emas, draw_position, fetch_recent, fit_ylim, move_text, new_figure,
                    price_tag, style_axes, time_ticks, to_png)
from shared.config import settings
from shared.logger import get_logger
from shared.timefmt import zona

log = get_logger(__name__)

BARS = 72        # velas que se ven en el gráfico (3 días en H1)
LOOKBACK = 120   # velas donde se buscan soportes y resistencias
PIVOT = 3        # velas a cada lado para que un máximo/mínimo cuente como giro
ZONE_COLORS = {"resistencia": "#c0392b", "soporte": "#2a78d6"}


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """Vela típica: promedio del rango verdadero de las últimas `period` velas."""
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return float(tr.tail(period).mean())


def key_levels(df: pd.DataFrame, per_side: int = 2) -> Dict[str, Any]:
    """Techo y piso de 24 h y las `per_side` resistencias (arriba) y soportes (abajo) más cercanos."""
    close, a = float(df["close"].iloc[-1]), atr(df)
    last24 = df.tail(24)
    win = df.tail(LOOKBACK)
    highs, lows = win["high"].to_numpy(), win["low"].to_numpy()
    pivots: List[Tuple[float, str]] = []
    for i in range(PIVOT, len(win) - PIVOT):
        if highs[i] == highs[i - PIVOT:i + PIVOT + 1].max():
            pivots.append((float(highs[i]), "max"))
        if lows[i] == lows[i - PIVOT:i + PIVOT + 1].min():
            pivots.append((float(lows[i]), "min"))
    # Agrupar giros cercanos (menos de media vela típica) en un solo nivel
    zones: List[List[float]] = []
    for price, _ in sorted(pivots):
        if zones and price - zones[-1][0] <= a * 0.5:  # contra el primero: la zona no se estira sin fin
            zones[-1].append(price)
        else:
            zones.append([price])
    levels = [{"precio": sum(z) / len(z), "toques": len(z)} for z in zones]
    min_gap = a * 0.25  # un nivel pegado al precio no sirve de referencia
    def pick(candidates):
        """Los más cercanos al precio, separados al menos una vela típica entre sí (si no, se ven como uno)."""
        out = []
        for lv in candidates:
            if all(abs(lv["precio"] - o["precio"]) >= a for o in out):
                out.append(lv)
            if len(out) == per_side:
                break
        return out

    res = pick(sorted((lv for lv in levels if lv["precio"] > close + min_gap), key=lambda lv: lv["precio"]))
    sup = pick(sorted((lv for lv in levels if lv["precio"] < close - min_gap), key=lambda lv: -lv["precio"]))
    return {"cierre": close, "atr": a, "techo_24h": float(last24["high"].max()),
            "piso_24h": float(last24["low"].min()), "resistencias": res, "soportes": sup}


def candle_anatomy(row: pd.Series, a: float) -> Dict[str, Any]:
    """Cuerpo y mechas de una vela y lo que suelen significar."""
    o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
    body, upper, lower = abs(c - o), h - max(o, c), min(o, c) - l
    rng = (h - l) or 1e-12
    notes = []
    if body <= rng * 0.25:
        notes.append("cuerpo pequeño: compradores y vendedores quedaron casi empatados (indecisión)")
    if upper >= max(body, 1e-12) * 1.5 and upper >= rng * 0.35:
        notes.append("mecha superior larga: el precio subió pero los vendedores lo devolvieron hacia abajo (rechazo arriba)")
    if lower >= max(body, 1e-12) * 1.5 and lower >= rng * 0.35:
        notes.append("mecha inferior larga: el precio bajó pero los compradores lo devolvieron hacia arriba (rechazo abajo)")
    if body >= rng * 0.7 and rng >= a:
        notes.append(f"vela grande y casi sin mechas: un lado ({'compradores' if c >= o else 'vendedores'}) "
                     "dominó toda la hora")
    return {"alcista": c >= o, "cuerpo": body, "mecha_sup": upper, "mecha_inf": lower, "rango": h - l,
            "notas": notes or ["vela normal, sin una señal clara de rechazo"]}


def level_lines(pair: str, sig: Dict[str, Any], lv: Dict[str, Any]) -> List[str]:
    """Los niveles en palabras, con la distancia al precio y si se cruzan en el camino al objetivo."""
    dg = digits(pair)
    close = lv["cierre"]
    lines = [f"Techo de 24 h: {lv['techo_24h']:.{dg}f} ({move_text(pair, lv['techo_24h'] - close)} arriba)",
             f"Piso de 24 h: {lv['piso_24h']:.{dg}f} ({move_text(pair, close - lv['piso_24h'])} abajo)"]
    for z in lv["resistencias"]:
        lines.append(f"Resistencia: {z['precio']:.{dg}f} ({z['toques']} toque(s), "
                     f"{move_text(pair, z['precio'] - close)} arriba)")
    for z in lv["soportes"]:
        lines.append(f"Soporte: {z['precio']:.{dg}f} ({z['toques']} toque(s), "
                     f"{move_text(pair, close - z['precio'])} abajo)")
    tp, buy = sig.get("take_profit"), str(sig.get("direction", "")).lower() == "buy"
    if isinstance(tp, (int, float)):
        blocking = [z for z in (lv["resistencias"] if buy else lv["soportes"])
                    if (close < z["precio"] < tp if buy else tp < z["precio"] < close)]
        if blocking:
            z = blocking[0]
            lines.append(f"Ojo: antes del objetivo hay {'una resistencia' if buy else 'un soporte'} en "
                         f"{z['precio']:.{dg}f}; ahí el precio puede frenar o rebotar.")
        else:
            lines.append(f"Camino al objetivo: {'ninguna resistencia marcada' if buy else 'ningún soporte marcado'} "
                         "entre la entrada y el objetivo.")
    return lines


def candle_lines(pair: str, an: Dict[str, Any]) -> List[str]:
    return [f"Última vela {'alcista (verde)' if an['alcista'] else 'bajista (naranja)'}: "
            f"cuerpo {move_text(pair, an['cuerpo'])}, mecha superior {move_text(pair, an['mecha_sup'])}, "
            f"mecha inferior {move_text(pair, an['mecha_inf'])}."] + [f"  · {n}" for n in an["notas"]]


def plot_signal(pair: str, full: pd.DataFrame, sig: Dict[str, Any], lv: Dict[str, Any], gran: str) -> bytes:
    df = full.tail(BARS)
    dg = digits(pair)
    buy = str(sig.get("direction", "")).lower() == "buy"
    entry = float(sig.get("entry_price") or lv["cierre"])
    sl, tp = sig.get("stop_loss"), sig.get("take_profit")
    n = len(df)
    future = 14  # espacio a la derecha para la operación y las etiquetas

    fig, ax = new_figure(size=(11, 6.6))
    # Techo y piso de 24 h: franja suave de las últimas 24 velas
    x24 = max(n - 24, 0) - 0.5
    for price, name in ((lv["techo_24h"], "Techo 24 h"), (lv["piso_24h"], "Piso 24 h")):
        ax.hlines(price, x24, n - 0.5, color=REFERENCE, linewidth=1.1, linestyles=(0, (6, 3)), zorder=2)
        ax.annotate(f"{name} {price:.{dg}f}", xy=(x24, price), xytext=(2, 3 if name.startswith("Techo") else -3),
                    textcoords="offset points", va="bottom" if name.startswith("Techo") else "top",
                    fontsize=7.5, color=TEXT_SECONDARY, zorder=9)
    # Soportes y resistencias: líneas a todo el ancho, más gruesas cuantos más toques
    for kind, zs in (("resistencia", lv["resistencias"]), ("soporte", lv["soportes"])):
        for z in zs:
            ax.axhline(z["precio"], color=ZONE_COLORS[kind], linewidth=0.8 + 0.4 * min(z["toques"], 4), alpha=0.75,
                       zorder=1)
            ax.annotate(f"{kind.capitalize()} {z['precio']:.{dg}f} · {z['toques']} toque(s)", xy=(0, z["precio"]),
                        xytext=(2, 3 if kind == "resistencia" else -3), textcoords="offset points",
                        va="bottom" if kind == "resistencia" else "top", fontsize=7.5,
                        color=ZONE_COLORS[kind], fontweight="bold", zorder=9,
                        bbox=dict(boxstyle="round,pad=0.15", facecolor=SURFACE, edgecolor="none", alpha=0.8))
    draw_candles(ax, df)
    draw_emas(ax, full, n)
    draw_position(ax, n - 0.5, n - 0.5 + future, pair, buy, entry,
                  float(sl) if isinstance(sl, (int, float)) else None,
                  float(tp) if isinstance(tp, (int, float)) else None)

    # Anatomía de la última vela: cuerpo y mechas señalados
    last = df.iloc[-1]
    o, h, l, c = (float(last[k]) for k in ("open", "high", "low", "close"))
    i = n - 1
    for y, text in ((h, "mecha superior"), ((o + c) / 2, "cuerpo"), (l, "mecha inferior")):
        ax.annotate(text, xy=(i, y), xytext=(i - 4, y), textcoords="data", ha="right", va="center", fontsize=7.5,
                    color=TEXT_PRIMARY, zorder=9, arrowprops=dict(arrowstyle="-|>", color=TEXT_SECONDARY, lw=0.8),
                    bbox=dict(boxstyle="round,pad=0.2", facecolor=SURFACE, edgecolor=GRID))

    extra = [entry, lv["techo_24h"], lv["piso_24h"]] + [z["precio"] for z in lv["resistencias"] + lv["soportes"]]
    extra += [float(v) for v in (sl, tp) if isinstance(v, (int, float))]
    fit_ylim(ax, df, extra)
    ax.set_xlim(-1, n + future + 1)
    time_ticks(ax, df.index, gran)
    style_axes(ax)
    price_tag(ax, df, pair)
    kind = {"practica": " · práctica"}.get(sig.get("strategy_name") or "", "")
    ax.set_title(f"{display_name(pair)} · señal de {'COMPRA' if buy else 'VENTA'}{kind}", loc="left",
                 fontsize=11.5, color=TEXT_PRIMARY, pad=10)
    fig.text(0.01, 0.01, f"Horas: {zona()}. Rojo: resistencias (techos donde el precio giró a la baja). Azul: "
             "soportes (pisos donde giró al alza). Gris: techo y piso de 24 h. Verde/naranja: hasta el objetivo "
             "y hasta el stop.", fontsize=7.2, color=TEXT_SECONDARY, wrap=True)
    fig.subplots_adjust(left=0.08, right=0.89, top=0.92, bottom=0.1)
    return to_png(fig)


GUIDE = [
    ("Vela", "cada vela es 1 hora. Verde: cerró más arriba de donde abrió (subió). Naranja: cerró más abajo (bajó)."),
    ("Cuerpo", "la parte gruesa: de la apertura al cierre. Cuerpo grande = un lado dominó la hora."),
    ("Mechas", "las líneas finas: hasta dónde llegó el precio y fue devuelto. Mecha larga arriba = rechazo de "
               "precios altos; mecha larga abajo = rechazo de precios bajos."),
    ("Resistencia (techo)", "precio donde antes el precio giró a la baja. Suele costar pasarlo; si lo rompe, "
                            "a veces se convierte en soporte."),
    ("Soporte (piso)", "precio donde antes el precio giró al alza. Si lo rompe, a veces se convierte en resistencia."),
    ("EMA 50 / 200", "promedios del precio. Por encima = tendencia alcista; por debajo = bajista."),
]


def _guide_candle(ax, x, o, h, l, c, width=0.5):
    color = UP if c >= o else DOWN
    ax.vlines(x, l, h, color=color, linewidth=2.2, zorder=2)
    lo, hi = sorted((o, c))
    ax.add_patch(plt.Rectangle((x - width / 2, lo), width, max(hi - lo, 0.04), facecolor=color, edgecolor=color,
                               zorder=3))


def _note(ax, text, xy, xytext, ha="left"):
    ax.annotate(text, xy=xy, xytext=xytext, ha=ha, va="center", fontsize=8.5, color=TEXT_PRIMARY,
                arrowprops=dict(arrowstyle="-|>", color=TEXT_SECONDARY, lw=0.8))


def _clean(ax, title):
    ax.set_title(title, loc="left", fontsize=11, color=TEXT_PRIMARY, pad=8)
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ax.spines.values():
        side.set_visible(False)


@lru_cache(maxsize=1)
def plot_guide() -> bytes:
    """Imagen fija de la guía: partes de una vela, velas de rechazo y soporte/resistencia."""
    fig, axes = new_figure(1, 3, size=(12, 4.6))
    # 1) Partes de una vela
    ax = axes[0]
    _guide_candle(ax, 1, o=2, h=5, l=0.6, c=4)
    _guide_candle(ax, 3.6, o=4, h=4.7, l=1, c=2)
    _note(ax, "máximo", (1, 5), (1.6, 5.3))
    _note(ax, "cierre", (1.25, 4), (1.9, 4.2))
    _note(ax, "cuerpo", (1.25, 3), (1.9, 3.1))
    _note(ax, "apertura", (1.25, 2), (1.9, 2.0))
    _note(ax, "mínimo", (1, 0.6), (1.6, 0.4))
    _note(ax, "mecha\nsuperior", (3.6, 4.35), (4.2, 5.2))
    _note(ax, "mecha\ninferior", (3.6, 1.5), (4.2, 0.8))
    ax.text(1, -0.6, "Verde: subió\n(cierre arriba\nde la apertura)", ha="center", fontsize=8, color=UP)
    ax.text(3.6, -0.6, "Naranja: bajó\n(cierre abajo\nde la apertura)", ha="center", fontsize=8, color=DOWN)
    ax.set_xlim(0, 5.4)
    ax.set_ylim(-1.5, 6)
    _clean(ax, "1. Partes de una vela")
    # 2) Velas que cuentan algo
    ax = axes[1]
    _guide_candle(ax, 0.9, o=3.6, h=4.0, l=0.6, c=3.9)
    _guide_candle(ax, 2.7, o=1.7, h=4.9, l=1.4, c=1.5)
    _guide_candle(ax, 4.5, o=2.75, h=4.2, l=1.3, c=2.8)
    _guide_candle(ax, 6.3, o=0.8, h=4.8, l=0.7, c=4.7)
    for x, title, body in ((0.9, "Rechazo\nabajo", "mecha inferior\nlarga: los\ncompradores\nempujaron\narriba"),
                           (2.7, "Rechazo\narriba", "mecha superior\nlarga: los\nvendedores\nempujaron\nabajo"),
                           (4.5, "Indecisión", "cuerpo\nmínimo:\nnadie ganó"),
                           (6.3, "Dominio", "cuerpo\ngrande sin\nmechas: un\nlado mandó")):
        ax.text(x, 5.2, title, ha="center", va="bottom", fontsize=8.5, fontweight="bold", color=TEXT_PRIMARY)
        ax.text(x, -0.2, body, ha="center", va="top", fontsize=7.5, color=TEXT_SECONDARY)
    ax.set_xlim(0, 7.2)
    ax.set_ylim(-2.6, 6)
    _clean(ax, "2. Velas que cuentan algo")
    # 3) Soporte y resistencia
    ax = axes[2]
    xs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    ys = [1.5, 3.9, 2.2, 3.95, 1.6, 3.4, 1.55, 3.0, 4.8, 4.05, 5.3]
    ax.plot(xs, ys, color=TEXT_PRIMARY, linewidth=1.6)
    ax.axhline(4.0, color=ZONE_COLORS["resistencia"], linewidth=2)
    ax.axhline(1.5, color=ZONE_COLORS["soporte"], linewidth=2)
    for x in (1, 3):
        ax.scatter([x], [ys[x]], s=40, color=ZONE_COLORS["resistencia"], zorder=4)
    for x in (0, 4, 6):
        ax.scatter([x], [ys[x]], s=40, color=ZONE_COLORS["soporte"], zorder=4)
    ax.text(0.1, 4.15, "Resistencia (techo): aquí giró a la baja", fontsize=8, color=ZONE_COLORS["resistencia"],
            fontweight="bold")
    ax.text(0.1, 1.1, "Soporte (piso): aquí giró al alza", fontsize=8, color=ZONE_COLORS["soporte"],
            fontweight="bold")
    _note(ax, "rompe el techo", (7.6, 4.0), (5.2, 5.4), ha="right")
    _note(ax, "el techo viejo\nahora es piso", (9, 4.05), (9.6, 2.6), ha="center")
    ax.set_xlim(-0.3, 10.5)
    ax.set_ylim(0.6, 6)
    _clean(ax, "3. Soporte y resistencia")
    fig.subplots_adjust(left=0.02, right=0.98, top=0.88, bottom=0.04, wspace=0.12)
    return to_png(fig)


def build_signal_email(sig: Dict[str, Any], candles: Optional[pd.DataFrame], gran: str) -> Tuple[str, str, str, Dict[str, bytes]]:
    """(asunto, html, texto, imágenes) del correo de señal. Sin velas: el texto de siempre sin gráfico."""
    from email_alerts import signal_message

    subject, text = signal_message(sig)
    pair = sig.get("pair", "UNKNOWN")
    images: Dict[str, bytes] = {}
    extra: List[str] = []
    if candles is not None and len(candles) >= 30:
        lv = key_levels(candles)
        an = candle_anatomy(candles.iloc[-1], lv["atr"])
        extra = (["", "GRÁFICO (ayuda para leerlo; el modelo no usa estos niveles):"]
                 + level_lines(pair, sig, lv) + candle_lines(pair, an))
        try:
            images["senal"] = plot_signal(pair, candles, sig, lv, gran)
        except Exception:
            log.exception("No se pudo dibujar la señal; se envía sin gráfico")
    try:
        images["guia"] = plot_guide()
    except Exception:
        log.exception("No se pudo dibujar la guía; se envía solo en texto")
    text_full = text + ("\n" + "\n".join(extra) if extra else "")
    guide_html = "".join(f"<li><b>{escape(k)}:</b> {escape(v)}</li>" for k, v in GUIDE)
    img = ('<img src="cid:senal" alt="Gráfico de la señal" style="width:100%;max-width:900px">'
           if "senal" in images else "")
    guide_img = ('<img src="cid:guia" alt="Guía: partes de una vela, velas de rechazo, soporte y resistencia" '
                 'style="width:100%;max-width:900px">' if "guia" in images else "")
    html = f"""<html><body style="font-family:Arial,sans-serif;background:{SURFACE};color:{TEXT_PRIMARY}">
<pre style="font-family:Arial,sans-serif;white-space:pre-wrap;font-size:14px">{escape(text_full)}</pre>
{img}
<h3 style="font-size:15px">Cómo leer el gráfico</h3>
{guide_img}
<ul style="font-size:13px;color:{TEXT_SECONDARY}">{guide_html}</ul>
<p style="font-size:12px;color:{TEXT_SECONDARY}">Los soportes, resistencias y mechas son una ayuda para entrenar el
ojo: el modelo no los usa para decidir. Lo que sí pesó está en "POR QUÉ".</p>
</body></html>"""
    return subject, html, text_full, images


def send_signal_report(email, sig: Dict[str, Any], now: Optional[datetime] = None,
                       fetch: Optional[Callable] = None) -> bool:
    """Correo de señal con gráfico; si algo falla, el aviso en texto de siempre."""
    if not settings.enable_email_signals:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        candles = None
        gran = settings.candle_granularity
        try:
            candles = fetch_recent(sig["pair"], gran, max(BARS, LOOKBACK), now, fetch=fetch)
        except Exception:
            log.exception("No se pudieron leer las velas para el gráfico de la señal")
        subject, html, text, images = build_signal_email(sig, candles, gran)
        return email.send_html(subject, html, text, images=images)
    except Exception:
        log.exception("No se pudo armar el correo de la señal; se envía el aviso en texto")
        email.send_signal_alert(sig)
        return False
