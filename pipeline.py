"""
Pipeline del modelo predictivo, de punta a punta, con un solo comando.

    python pipeline.py                     # todo: datos -> validar -> entrenar -> señales (una pasada)
    python pipeline.py datos               # descarga/actualiza velas de Dukascopy en data/
    python pipeline.py validar             # walk-forward + informe en reports/
    python pipeline.py entrenar            # modelo final con toda la historia -> models/
    python pipeline.py senales             # paper trading: procesa las velas nuevas y termina
    python pipeline.py senales --continuo  # paper trading: revisa cada minuto hasta Ctrl+C

Todo se configura en config.yaml. El paper trading SIMULA las órdenes: no se
conecta a ningún broker. Operar una cuenta (demo o real) es cosa del pipeline
de microservicios, protegido por DRY_RUN y ALLOW_LIVE_TRADING en .env.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in ("services/strategy-engine/src", "services/risk-manager/src", "backtesting/src", "."):
    sys.path.insert(0, str(ROOT / p))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from data_loader import load_from_dukascopy
from shared.config import load_trading_config

MODELS_DIR = ROOT / "models"


def model_path(pair: str, gran: str) -> Path:
    return MODELS_DIR / f"ml_{pair}_{gran}.joblib"


def step_datos(cfg):
    m = cfg["mercado"]
    df = load_from_dukascopy(m["par"], m["temporalidad"], years=m["anios_historia"])
    print(f"[v] Datos: {m['par']} {m['temporalidad']} | {len(df):,} velas | última cerrada {df.index[-1]} UTC")
    return df


def step_validar(cfg):
    from run_validation import run

    report, out_dir = run(["ml", "ma_crossover"], refresh_data=False)
    return report.verdicts.get("ml"), out_dir


def step_entrenar(cfg, verdict=None):
    from strategies.ml_strategy import MLStrategy

    m = cfg["mercado"]
    df = load_from_dukascopy(m["par"], m["temporalidad"], refresh=False)
    strategy = MLStrategy.train(df, cfg, m["par"])
    path = model_path(m["par"], m["temporalidad"])
    strategy.model.save(path)

    meta = dict(strategy.model.meta)
    meta.update(par=m["par"], temporalidad=m["temporalidad"], config=cfg)
    if verdict is not None:
        meta["validacion"] = {"veredicto": verdict.label, "expectativa_r": verdict.expectancy_r,
                              "p_valor": verdict.p_value, "operaciones": verdict.n_trades}
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"[v] Modelo final entrenado con {meta['muestras']:,} muestras ({meta['desde'][:10]} a {meta['hasta'][:10]}) -> {path}")
    return path


def _paper_logger(log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "paper.log"

    def log(msg: str):
        line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC | {msg}"
        print(line, flush=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    return log


def step_senales(cfg, continuo: bool = False, intervalo: int = 60):
    from ml.model import TradeModel
    from paper_trader import PaperTrader
    from strategies.ml_strategy import MLStrategy

    m = cfg["mercado"]
    path = model_path(m["par"], m["temporalidad"])
    if not path.exists():
        sys.exit(f"[x] No hay modelo entrenado en {path}. Ejecuta: python pipeline.py entrenar")

    meta_path = path.with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    strategy = MLStrategy.from_config(TradeModel.load(path), cfg, m["par"])
    log_dir = ROOT / cfg["paper_trading"]["carpeta_logs"]
    log = _paper_logger(log_dir)
    trader = PaperTrader(strategy, m["par"], cfg, log_dir, log=log)

    verdict = meta.get("validacion", {}).get("veredicto", "sin validar")
    log(f"PAPER TRADING (simulado, sin broker) | {m['par']} {m['temporalidad']} | modelo entrenado hasta "
        f"{meta.get('hasta', '?')[:16]} | veredicto de la validación: {verdict}")
    if verdict != "VENTAJA CON EVIDENCIA":
        log("AVISO: el modelo no tiene ventaja probada fuera de muestra. Úsalo solo para observar, no con dinero real.")

    while True:
        try:
            df = load_from_dukascopy(m["par"], m["temporalidad"], years=m["anios_historia"])
            n = trader.process(df)
            if n:
                log(f"{n} vela(s) nueva(s) procesada(s). {trader.summary()}")
        except Exception as e:  # sin conexión, Dukascopy caído...: se reintenta en la próxima vuelta
            log(f"ERROR al actualizar datos o procesar velas: {e}")
            if not continuo:
                raise
        if not continuo:
            log(trader.summary())
            return trader
        time.sleep(intervalo)


def main():
    parser = argparse.ArgumentParser(description="Pipeline del modelo: datos, validación, entrenamiento y paper trading")
    parser.add_argument("paso", nargs="?", default="todo", choices=["todo", "datos", "validar", "entrenar", "senales"])
    parser.add_argument("--continuo", action="store_true", help="senales: seguir revisando velas nuevas hasta Ctrl+C")
    parser.add_argument("--intervalo", type=int, default=60, help="senales --continuo: segundos entre revisiones")
    args = parser.parse_args()

    cfg = load_trading_config()
    if args.paso == "datos":
        step_datos(cfg)
    elif args.paso == "validar":
        step_validar(cfg)
    elif args.paso == "entrenar":
        step_entrenar(cfg)
    elif args.paso == "senales":
        try:
            step_senales(cfg, continuo=args.continuo, intervalo=args.intervalo)
        except KeyboardInterrupt:
            print("\n[i] Paper trading detenido. El estado queda guardado; se retoma donde quedó.")
    else:
        step_datos(cfg)
        verdict, out_dir = step_validar(cfg)
        step_entrenar(cfg, verdict)
        step_senales(cfg)
        print(f"\n[v] Pipeline completo. Informe: {out_dir / 'informe.md'}")


if __name__ == "__main__":
    main()
