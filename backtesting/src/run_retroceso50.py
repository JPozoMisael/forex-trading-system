"""
Prueba ÚNICA del método "retroceso al 50 %" en oro M5, PRE-REGISTRADO en
config.yaml (sección retroceso_50, 2026-11-06).

    python backtesting/src/run_retroceso50.py            # prueba + informe + correo (HTML) en reports/
    python backtesting/src/run_retroceso50.py --enviar   # además lo envía por email (necesita EMAIL_* en .env)

Escribe reports/<fecha>_retroceso50/: informe.md (técnico), correo.html (para
cualquier persona) y los gráficos de las operaciones de las 2 últimas semanas.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(ROOT, "services/strategy-engine/src"))
sys.path.insert(0, os.path.join(ROOT, "services/risk-manager/src"))
sys.path.insert(0, os.path.join(ROOT, "services/monitoring/src"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import numpy as np
import pandas as pd

from retroceso50 import Params, simulate
from run_instrumentos import PAPER_VERDICTS
from shared.config import load_trading_config
from validation import make_verdict

RISK_PCT = 1.0          # % de la cuenta por operación, como el sistema
CAPITAL = 10_000.0      # cuenta de ejemplo para traducir R a dinero
REVIEW_DAYS = 14


def period_stats(trades: pd.DataFrame, val: dict) -> dict:
    """Resultado de un periodo: operaciones, aciertos, R, cuenta con 1 % de riesgo, peor caída y veredicto."""
    v = make_verdict(trades, int(val["n_permutaciones"]), int(val["min_operaciones"]),
                     float(val["expectativa_minima_r"]))
    if trades.empty:
        return {"n": 0, "win": float("nan"), "exp": float("nan"), "ret": 0.0, "dd": 0.0, "racha": 0, "v": v,
                "por_mes": 0.0}
    equity = CAPITAL * np.cumprod(1 + RISK_PCT / 100 * trades["r"].to_numpy())
    peak = np.maximum.accumulate(np.r_[CAPITAL, equity])[1:]
    losing = (trades["r"] < 0).astype(int).to_numpy()
    streak = max((len(s) for s in "".join(map(str, losing)).split("0")), default=0)
    months = max(1.0, (trades["entry_time"].iloc[-1] - trades["entry_time"].iloc[0]).days / 30.4)
    return {"n": len(trades), "win": float((trades["r"] > 0).mean()), "exp": float(trades["r"].mean()),
            "ret": float(equity[-1] / CAPITAL - 1) * 100, "dd": float(((peak - equity) / peak).max()) * 100,
            "racha": streak, "v": v, "por_mes": len(trades) / months}


def eligible(dev: dict, fin: dict) -> bool:
    return all(s["v"].label in PAPER_VERDICTS and s["ret"] > 0 for s in (dev, fin))


def trade_charts(df: pd.DataFrame, trades: pd.DataFrame, out_dir: Path) -> list:
    from charts import plot_trade

    files = []
    for k, t in enumerate(trades.itertuples(), 1):
        i0, i1 = df.index.get_loc(t.placed_time), df.index.get_loc(t.exit_time)
        window = df.iloc[max(0, i0 - 60): min(len(df), i1 + 12)]
        units = RISK_PCT / 100 * CAPITAL / t.riesgo  # onzas para arriesgar el 1 %
        png = plot_trade("XAU_USD", window, "M5", {
            "direction": t.side, "entry_price": t.entry_price, "exit_price": t.exit_price,
            "opened_at": t.entry_time, "closed_at": t.exit_time, "stop_loss": t.stop_loss,
            "take_profit": t.take_profit, "pnl": t.r * RISK_PCT / 100 * CAPITAL, "money_per_unit": units})
        name = f"operacion_{k:02d}.png"
        (out_dir / name).write_bytes(png)
        files.append(name)
    return files


def report_md(dev, fin, n_tests, recent, cfg) -> str:
    def row(name, s):
        v = s["v"]
        return (f"| {name} | {s['n']} | {s['por_mes']:.1f} | {s['win'] * 100:.0f} % | {s['exp']:+.3f} R | "
                f"{s['ret']:+.1f} % | {s['dd']:.1f} % | {v.p_value:.4f} | {v.label} |")

    return "\n".join([
        f"# Retroceso al 50 % en oro M5 · {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
        "Pre-registrado en `config.yaml` (sección `retroceso_50`, commit 40c8ae6) antes de programarlo. Una sola "
        "pasada. Costes del oro (spread 0.50 + slippage 0.10 USD/oz) y comisión 7 USD/lote.", "",
        "| periodo | ops | ops/mes | aciertos | R medio | cuenta (1 % riesgo) | peor caída | p | veredicto |",
        "|---|---|---|---|---|---|---|---|---|",
        row(f"Desarrollo (hasta {cfg['validacion']['inicio_prueba_final']})", dev),
        row("Prueba final", fin), "",
        f"**Cumple el criterio para paper trading:** {'SÍ' if eligible(dev, fin) else 'NO'}. "
        f"Dinero real exige además VENTAJA CON EVIDENCIA en la final con p < 0.05 / {n_tests} = {0.05 / n_tests:.5f}.",
        "", f"Operaciones de las últimas {REVIEW_DAYS} días: {len(recent)} (ejemplo, no cambian el veredicto).",
    ])


def email_html(dev, fin, recent, charts, cfg) -> tuple:
    """(asunto, html, texto) para cualquier persona: sin jerga, con dinero de una cuenta de ejemplo."""
    from charts import DOWN, GRID, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, UP
    from shared.timefmt import fmt_local, zona

    ok = eligible(dev, fin)
    usd = lambda r: f"{'+' if r >= 0 else '−'}${abs(r) * RISK_PCT / 100 * CAPITAL:,.0f}"
    verdict = ("✅ El método GANÓ en los dos periodos de prueba. Pasa a la simulación en vivo para confirmarlo."
               if ok else "❌ El método NO ganó de forma consistente con costos reales. No conviene operarlo con dinero.")
    subject = f"Probamos el método de tu amigo en el oro (6 años): {'funciona en la prueba' if ok else 'no funciona'}"

    def stat_rows(s):
        return [("Operaciones", f"{s['n']} (unas {s['por_mes']:.0f} al mes)"),
                ("Ganadoras", f"{s['win'] * 100:.0f} de cada 100"),
                ("Resultado de la cuenta de $10,000", f"{s['ret']:+.1f} %"),
                ("Peor caída desde un máximo", f"−{s['dd']:.1f} %"),
                ("Peor racha de pérdidas seguidas", f"{s['racha']}")]

    def table(rows, head=None):
        th = ("<tr>" + "".join(f"<th style='text-align:left;padding:6px 10px;color:{TEXT_SECONDARY};"
                               f"border-bottom:1px solid {GRID}'>{h}</th>" for h in head) + "</tr>") if head else ""
        trs = "".join("<tr>" + "".join(f"<td style='padding:6px 10px;border-bottom:1px solid {GRID}'>{c}</td>"
                                       for c in r) + "</tr>" for r in rows)
        return f"<table style='border-collapse:collapse;font-size:14px'>{th}{trs}</table>"

    p = "style='font-size:15px;line-height:1.5;margin:0 0 12px'"
    h2 = f"style='font-size:18px;margin:28px 0 10px;color:{TEXT_PRIMARY}'"
    side = {"buy": "COMPRA", "sell": "VENTA"}
    reason = {"tp": "llegó al objetivo", "sl": "tocó el stop", "tiempo": "se cerró a las 4 h", "fin_datos": "aún abierta"}
    recent_rows = [[fmt_local(t.entry_time), side[t.side], f"{t.entry_price:.2f}", f"{t.stop_loss:.2f}",
                    f"{t.take_profit:.2f}", reason[t.exit_reason],
                    f"<b style='color:{UP if t.r >= 0 else DOWN}'>{usd(t.r)}</b>"] for t in recent.itertuples()]
    recent_total = usd(recent["r"].sum()) if len(recent) else "$0"
    imgs = "".join(f"<p {p}><b>Operación {k}</b></p><img src='cid:{name[:-4]}' alt='Operación {k}' "
                   f"style='width:100%;max-width:820px;margin-bottom:16px'>" for k, name in enumerate(charts, 1))

    html = f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:{TEXT_PRIMARY};background:{SURFACE};max-width:860px;margin:0 auto;padding:16px">
<h1 style="font-size:22px;margin:0 0 8px">¿Funciona el método de tu amigo en el oro?</h1>
<p style="font-size:17px;line-height:1.5;padding:12px 14px;border-left:4px solid {UP if ok else DOWN};background:#f4f3f0">{verdict}</p>

<h2 {h2}>1. Qué hizo tu amigo</h2>
<p {p}>El 4 de noviembre, en una cuenta demo, vio que el oro subió de golpe de 4 040 a 4 071. Cuando el precio
retrocedió justo <b>a la mitad de esa subida (4 055)</b>, compró. Puso el stop muy cerca (2 dólares más abajo) y el
objetivo cerca del máximo (4 065). Arriesgó poco para ganar mucho: unos $20 para ganar $100. Ganó esa y otra
operación parecida en venta.</p>
<p {p}>Dos operaciones ganadoras no prueban que el método funcione: también hay que contar las que pierden. Por eso
lo probamos con miles de operaciones.</p>

<h2 {h2}>2. Cómo lo probamos</h2>
<p {p}>Escribimos la regla exacta <b>antes</b> de mirar ningún resultado, para no acomodarla:</p>
<ol style="font-size:15px;line-height:1.6">
<li>Cuando el oro se mueve fuerte en una dirección (en velas de 5 minutos, dentro de las últimas 4 horas)…</li>
<li>…se deja una orden esperando en <b>la mitad de ese movimiento</b>.</li>
<li>Stop un poco más allá del 61.8 % del movimiento; objetivo en el extremo del movimiento.</li>
<li>Se cobran los costos reales del oro (spread, deslizamiento y comisión).</li>
</ol>
<p {p}>Lo probamos con <b>6 años de precios del oro</b>, en dos partes por separado: los primeros 3 años y los
últimos 3. Un método bueno tiene que ganar en las dos. Para pasarlo a dinero, imaginamos una cuenta de $10,000 que
arriesga el 1 % ($100) en cada operación, igual que el sistema.</p>

<h2 {h2}>3. Resultado</h2>
<p {p}><b>Primeros 3 años</b> (hasta octubre de 2023)</p>
{table(stat_rows(dev))}
<p {p}><br><b>Últimos 3 años</b> (octubre de 2023 a hoy)</p>
{table(stat_rows(fin))}

<h2 {h2}>4. Las oportunidades de las últimas 2 semanas</h2>
<p {p}>Estas son todas las veces que la regla habría entrado en los últimos {REVIEW_DAYS} días, con lo que habría
pasado (cuenta de $10,000, arriesgando $100 por operación). Horas en {zona()}. En total: <b>{recent_total}</b>.</p>
{table(recent_rows, ["Entrada", "Tipo", "Precio", "Stop", "Objetivo", "Qué pasó", "Resultado"]) if recent_rows else f"<p {p}>No hubo ninguna.</p>"}
<p {p}>En cada gráfico: zona verde hasta el objetivo, zona naranja hasta el stop, ▲/▼ la
entrada y X la salida.</p>
{imgs}

<h2 {h2}>5. Qué significa</h2>
<p {p}>{"El método pasó la prueba con datos que no se usaron para diseñarlo. El siguiente paso es dejarlo correr en la simulación del sistema durante unas semanas antes de pensar en dinero real." if ok else "Con costos reales, las operaciones ganadoras no alcanzan a pagar las perdedoras de forma consistente. Que tu amigo haya ganado esas dos es posible, pero en miles de casos la regla no sostiene la ganancia. Para cambiar la conclusión haría falta ver su historial completo de varios meses (incluidas las pérdidas)."}</p>
<p style="font-size:12px;color:{TEXT_SECONDARY};margin-top:24px">Prueba pre-registrada en config.yaml (sección retroceso_50). Resultados de simulación con datos históricos de Dukascopy; no garantizan resultados futuros.</p>
</body></html>"""
    text = (f"{subject}\n\n{verdict}\n\nPrimeros 3 años: {dev['n']} operaciones, {dev['ret']:+.1f} %.\n"
            f"Últimos 3 años: {fin['n']} operaciones, {fin['ret']:+.1f} %.\n"
            f"Últimas 2 semanas: {len(recent)} operaciones, {recent_total}.")
    return subject, html, text


def main():
    from data_loader import load_from_dukascopy
    from report import REPORTS_DIR

    parser = argparse.ArgumentParser(description="Prueba del retroceso al 50 % en oro M5 (pre-registrada)")
    parser.add_argument("--enviar", action="store_true", help="envía el correo (necesita EMAIL_* en .env)")
    args = parser.parse_args()

    cfg = load_trading_config()
    sec = cfg["retroceso_50"]
    pair, gran = sec["instrumento"], sec["temporalidad"]
    n_tests = int(sec["pruebas_previas"]) + 1
    df = load_from_dukascopy(pair, gran, years=float(sec["anios"]), refresh=False)
    print(f"[v] {pair} {gran}: {len(df):,} velas [{df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}]")

    trades = simulate(df, pair, Params.from_config(sec), float(cfg["costes"]["comision_por_lote"]))
    cutoff = pd.Timestamp(cfg["validacion"]["inicio_prueba_final"], tz="UTC")
    dev = period_stats(trades[trades["entry_time"] < cutoff].reset_index(drop=True), cfg["validacion"])
    fin = period_stats(trades[trades["entry_time"] >= cutoff].reset_index(drop=True), cfg["validacion"])
    recent = trades[trades["entry_time"] >= df.index[-1] - timedelta(days=REVIEW_DAYS)].reset_index(drop=True)

    out_dir = REPORTS_DIR / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}_retroceso50"
    out_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_dir / "operaciones.csv", index=False)
    charts = trade_charts(df, recent, out_dir)
    md = report_md(dev, fin, n_tests, recent, cfg)
    (out_dir / "informe.md").write_text(md, encoding="utf-8")
    subject, html, text = email_html(dev, fin, recent, charts, cfg)
    local = html
    for name in charts:
        local = local.replace(f"cid:{name[:-4]}", name)
    (out_dir / "correo.html").write_text(local, encoding="utf-8")
    print("\n" + md + f"\n\n[v] Informe: {out_dir / 'informe.md'}\n[v] Correo: {out_dir / 'correo.html'}")

    if args.enviar:
        from email_alerts import EmailNotifier

        images = {name[:-4]: (out_dir / name).read_bytes() for name in charts}
        sent = EmailNotifier().send_html(subject, html, text, images=images)
        print("[v] Correo enviado" if sent else "[x] No se pudo enviar el correo (ver log)")


if __name__ == "__main__":
    main()
