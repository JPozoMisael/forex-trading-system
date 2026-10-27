"""
Paper trading: aplica una estrategia a velas reales que van llegando y SIMULA
las órdenes. No se conecta a ningún broker ni envía órdenes.

Usa exactamente las reglas del backtester (BacktestEngine): la señal de la vela
t cerrada se ejecuta a la apertura de t+1, spread completo, slippage,
comisión, SL si SL y TP caen en la misma vela y límite de pérdida diaria. Así
lo que se ve en paper es comparable con el backtest.

Archivos en la carpeta de logs:
  estado.json      saldo, posición abierta, señal pendiente y última vela procesada
  senales.csv      cada señal generada
  operaciones.csv  cada operación cerrada
  equity.csv       equity al cierre de cada vela
"""
import csv
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from engine import BacktestEngine
from shared.fx import get_pip_size, quote_to_usd_rate
from shared.models import OrderSide, Signal, SignalDirection
from strategies.base_strategy import BaseStrategy


class PaperTrader:
    def __init__(
        self,
        strategy: BaseStrategy,
        pair: str,
        cfg: Dict[str, Any],
        log_dir: Path,
        log: Callable[[str], None] = print,
    ):
        self.strategy = strategy
        self.pair = pair
        self.log_dir = Path(log_dir)
        self.log = log
        c, r = cfg["costes"], cfg["riesgo"]
        self.engine = BacktestEngine(
            initial_balance=float(r["capital_inicial"]),
            risk_per_trade_pct=float(r["riesgo_por_operacion_pct"]),
            spread_pips=float(c["spread_pips"]),
            slippage_pips=float(c["slippage_pips"]),
            commission_per_lot=float(c["comision_por_lote"]),
            max_daily_loss_pct=float(r["perdida_diaria_max_pct"]),
        )
        pip = get_pip_size(pair)
        self.half_spread = self.engine.spread_pips * pip / 2
        self.slip = self.engine.slippage_pips * pip
        self.pip = pip
        self.state = self._load_state()

    # ------------------------------------------------------------------ estado
    @property
    def state_path(self) -> Path:
        return self.log_dir / "estado.json"

    def _new_state(self) -> Dict[str, Any]:
        return {
            "par": self.pair,
            "estrategia": self.strategy.name,
            "balance": self.engine.initial_balance,
            "equity": self.engine.initial_balance,
            "posicion": None,
            "pendiente": None,
            "ultima_vela": None,
            "dia": None,
            "equity_inicio_dia": self.engine.initial_balance,
            "dia_bloqueado": None,
            "operaciones": 0,
        }

    def _load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return self._new_state()

    def _save_state(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2, default=str), encoding="utf-8")

    def _append_csv(self, name: str, row: Dict[str, Any]):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / name
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row))
            if new:
                writer.writeheader()
            writer.writerow(row)

    @staticmethod
    def _trade_to_json(trade: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(trade)
        out["side"] = trade["side"].value
        out["entry_time"] = str(trade["entry_time"])
        out["signal_time"] = str(trade["signal_time"])
        return out

    @staticmethod
    def _trade_from_json(data: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(data)
        out["side"] = OrderSide(data["side"])
        out["entry_time"] = pd.Timestamp(data["entry_time"])
        out["signal_time"] = pd.Timestamp(data["signal_time"])
        return out

    # --------------------------------------------------------------- proceso
    def start(self, history: pd.DataFrame):
        """Primera ejecución: marca la última vela como procesada sin operar el pasado."""
        if self.state["ultima_vela"] is None:
            self.state["ultima_vela"] = str(history.index[-1])
            self._save_state()
            self.log(f"Paper trading iniciado en la vela {history.index[-1]} con ${self.state['balance']:,.2f}")

    def process(self, df: pd.DataFrame) -> int:
        """Procesa en orden las velas cerradas nuevas de `df`. Devuelve cuántas procesó."""
        self.start(df)
        last = pd.Timestamp(self.state["ultima_vela"])
        positions = [i for i, ts in enumerate(df.index) if ts > last]
        for i in positions:
            self._step(df, i)
        if positions:
            self._save_state()
        return len(positions)

    def _step(self, df: pd.DataFrame, i: int):
        st = self.state
        ts = df.index[i]
        o, h, l, c = (float(df[k].iloc[i]) for k in ("open", "high", "low", "close"))

        # 0. Cambio de día UTC: la equity al cierre previo es la base del límite diario
        day = str(ts.date())
        if day != st["dia"]:
            st["dia"] = day
            st["equity_inicio_dia"] = st["equity"]

        # 1. Señal pendiente -> se ejecuta a la apertura de esta vela
        if st["pendiente"] is not None:
            sig = Signal(**st["pendiente"])
            trade = self.engine._open_trade(sig, o, ts, st["balance"], self.pair, self.pip, self.half_spread, self.slip)
            st["pendiente"] = None
            if trade is not None:
                st["posicion"] = self._trade_to_json(trade)
                self.log(f"ABRE {trade['side'].value.upper()} {trade['units']} u. a {trade['entry_price']:.5f} | "
                         f"SL {trade['stop_loss']:.5f} TP {trade['take_profit']:.5f}")

        # 2. SL / TP de la posición abierta (incluida la recién abierta)
        if st["posicion"] is not None:
            trade = self._trade_from_json(st["posicion"])
            exit_price, reason = BacktestEngine._check_exit(trade, o, h, l, self.half_spread, self.slip)
            if reason is not None:
                self._close(trade, exit_price, ts, reason)

        # 3. Equity al cierre, valuada al precio al que se cerraría
        floating = 0.0
        if st["posicion"] is not None:
            trade = self._trade_from_json(st["posicion"])
            mark = c - self.half_spread if trade["side"] == OrderSide.BUY else c + self.half_spread
            diff = (mark - trade["entry_price"]) if trade["side"] == OrderSide.BUY else (trade["entry_price"] - mark)
            floating = diff * trade["units"] * quote_to_usd_rate(self.pair, c)
        st["equity"] = st["balance"] + floating
        self._append_csv("equity.csv", {"vela": ts, "balance": round(st["balance"], 2), "equity": round(st["equity"], 2),
                                        "posicion_abierta": st["posicion"] is not None})

        # 4. Límite de pérdida diaria (como el circuit breaker en vivo)
        limit = self.engine.max_daily_loss_pct
        if limit is not None and st["equity_inicio_dia"] > 0:
            loss = max(0.0, st["equity_inicio_dia"] - st["equity"]) / st["equity_inicio_dia"] * 100.0
            if loss >= limit and st["dia_bloqueado"] != day:
                st["dia_bloqueado"] = day
                self.log(f"LÍMITE DIARIO alcanzado ({loss:.2f}% >= {limit}%): sin operaciones nuevas hasta mañana (UTC)")
        blocked = st["dia_bloqueado"] == day

        # 5. Nueva señal con la vela cerrada; se ejecuta en la siguiente
        st["ultima_vela"] = str(ts)
        if st["posicion"] is None and not blocked and st["balance"] > 100:
            hist = df.iloc[max(0, i + 1 - self.strategy.lookback_window): i + 1]
            if len(hist) < self.strategy.min_candles_required:
                return
            sig = self.strategy.evaluate(hist, pair=self.pair)
            if sig and sig.direction in (SignalDirection.BUY, SignalDirection.SELL):
                st["pendiente"] = sig.model_dump(mode="json")
                self._append_csv("senales.csv", {
                    "vela": ts, "direccion": sig.direction.value, "confianza": sig.confidence,
                    "precio_referencia": sig.entry_price, "stop_loss": sig.stop_loss, "take_profit": sig.take_profit,
                    "p_long": sig.metadata.get("p_long"), "p_short": sig.metadata.get("p_short"),
                })
                self.log(f"SEÑAL {sig.direction.value.upper()} en la vela {ts} (confianza {sig.confidence:.3f}); "
                         f"se ejecuta a la apertura de la siguiente")

    def _close(self, trade: Dict[str, Any], exit_price: float, ts, reason: str):
        st = self.state
        pnl, comm, diff = self.engine.trade_pnl(trade, exit_price, self.pair)
        st["balance"] += pnl
        st["posicion"] = None
        st["operaciones"] += 1
        risk = abs(trade["entry_price"] - trade["stop_loss"]) * trade["units"] * quote_to_usd_rate(self.pair, trade["entry_price"])
        self._append_csv("operaciones.csv", {
            "id": st["operaciones"], "par": self.pair, "lado": trade["side"].value, "unidades": trade["units"],
            "senal": trade["signal_time"], "entrada": trade["entry_time"], "precio_entrada": round(trade["entry_price"], 5),
            "salida": ts, "precio_salida": round(exit_price, 5), "motivo": reason,
            "pips": round(diff / self.pip, 1), "comision": round(comm, 2), "pnl": round(pnl, 2),
            "r": round(pnl / risk, 3) if risk else 0.0, "balance": round(st["balance"], 2),
        })
        self.log(f"CIERRA {trade['side'].value.upper()} por {reason.upper()} a {exit_price:.5f} | "
                 f"PnL ${pnl:+,.2f} | balance ${st['balance']:,.2f}")

    def summary(self) -> str:
        st = self.state
        pos = st["posicion"]
        pos_txt = f"{pos['side'].upper()} desde {pos['entry_time']}" if pos else "sin posición"
        pend = " | señal pendiente para la próxima vela" if st["pendiente"] else ""
        return (f"Última vela {st['ultima_vela']} | balance ${st['balance']:,.2f} | equity ${st['equity']:,.2f} | "
                f"{st['operaciones']} operaciones cerradas | {pos_txt}{pend}")
