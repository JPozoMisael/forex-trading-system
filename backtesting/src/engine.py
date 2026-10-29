"""
Motor de Simulación de Backtesting orientada a eventos.

Convenciones de ejecución (las mismas que se esperan en vivo):
  - Las velas son precios MEDIOS (OANDA price="M"). Se compra al ask (medio +
    medio spread) y se vende al bid (medio - medio spread): el spread completo
    se paga entre la entrada y la salida.
  - La señal se calcula con la vela i CERRADA y se ejecuta a la APERTURA de la
    vela i+1. El SL/TP conservan la distancia que pidió la estrategia, medida
    desde el precio real de entrada (igual que MT5Broker y SimulatedBroker).
  - El slippage se paga en la entrada y en todas las salidas (SL, TP y cierre).
  - Si en una misma vela se tocan el SL y el TP, se asume el SL.
  - Comisión por lote estándar (100k unidades), ida y vuelta.
  - Límite de pérdida diaria sobre la equity (incluye flotante), como el
    circuit breaker del risk-manager: al alcanzarlo no se abren operaciones
    nuevas hasta el siguiente día UTC.
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import pandas as pd

import sys
import os

# Permitir imports desde services y shared
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/strategy-engine/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/risk-manager/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from shared.models import Signal, SignalDirection, OrderSide
from strategies.base_strategy import BaseStrategy
from position_sizing import calculate_position_size, get_pip_size
from shared.fx import contract_size, financing_cost_usd, quote_to_usd_rate
from metrics import BacktestMetrics, calculate_metrics


@dataclass
class BacktestResult:
    strategy_name: str
    pair: str
    metrics: BacktestMetrics
    trades: List[Dict[str, Any]]
    equity_series: pd.Series
    trades_df: pd.DataFrame


class BacktestEngine:
    def __init__(
        self,
        initial_balance: float = 10000.0,
        risk_per_trade_pct: float = 1.0,
        spread_pips: float = 1.2,
        slippage_pips: float = 0.3,
        commission_per_lot: float = 0.0,
        max_daily_loss_pct: Optional[float] = None,
        default_sl_pips: float = 20.0,
        default_tp_pips: float = 40.0,
        financing_annual_pct: float = 0.0,
    ):
        self.initial_balance = initial_balance
        self.risk_per_trade_pct = risk_per_trade_pct
        self.spread_pips = spread_pips
        self.slippage_pips = slippage_pips
        self.commission_per_lot = commission_per_lot  # USD por lote estándar, ida y vuelta
        self.max_daily_loss_pct = max_daily_loss_pct  # None = sin límite
        self.default_sl_pips = default_sl_pips
        self.default_tp_pips = default_tp_pips
        self.financing_annual_pct = financing_annual_pct  # swap aproximado, % anual del nocional (0 = no se cobra)

    def run(
        self,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        pair: str = "EUR_USD",
    ) -> BacktestResult:
        """
        Ejecuta el backtest iterativo sobre el DataFrame proporcionado.
        """
        if df.empty or len(df) < strategy.min_candles_required:
            raise ValueError(f"DataFrame insuficiente ({len(df)} velas, se requieren al menos {strategy.min_candles_required})")
        if quote_to_usd_rate(pair, float(df["close"].iloc[-1])) is None:
            raise ValueError(f"{pair}: el backtester solo soporta pares con USD (XXX_USD o USD_XXX)")

        pip_size = get_pip_size(pair)
        half_spread = self.spread_pips * pip_size / 2
        slip = self.slippage_pips * pip_size

        balance = self.initial_balance
        equity_records = {}
        trades: List[Dict[str, Any]] = []

        active_trade: Optional[Dict[str, Any]] = None
        pending_signal: Optional[Signal] = None
        min_bars = strategy.min_candles_required

        current_day = None
        day_start_equity = balance
        equity = balance
        blocked_days = set()

        def close_trade(trade: Dict[str, Any], exit_price: float, exit_time, reason: str):
            nonlocal balance
            pnl, comm, diff = self.trade_pnl(trade, exit_price, pair, exit_time)
            balance += pnl
            trades.append(
                {
                    "trade_id": len(trades) + 1,
                    "pair": pair,
                    "strategy": strategy.name,
                    "side": trade["side"].value,
                    "units": trade["units"],
                    "signal_time": trade["signal_time"],
                    "entry_time": trade["entry_time"],
                    "entry_price": round(trade["entry_price"], 5),
                    "exit_time": exit_time,
                    "exit_price": round(exit_price, 5),
                    "stop_loss": round(trade["stop_loss"], 5),
                    "take_profit": round(trade["take_profit"], 5),
                    "commission": round(comm, 2),
                    "swap": round(self.swap_usd(trade, exit_time, pair), 2),
                    "pnl": round(pnl, 2),
                    "pips": round(diff / pip_size, 1),
                    "roi_pct": round(pnl / trade["risk_usd"] * 100, 1) if trade["risk_usd"] else 0.0,
                    "exit_reason": reason,
                    "balance_after": round(balance, 2),
                }
            )

        for i in range(min_bars, len(df)):
            bar_time = df.index[i]
            o, h, l, c = (float(df[k].iloc[i]) for k in ("open", "high", "low", "close"))

            # 0. Cambio de día UTC: la equity al cierre previo es la base del límite diario
            day = bar_time.date() if hasattr(bar_time, "date") else None
            if day != current_day:
                current_day = day
                day_start_equity = equity

            # 1. Ejecutar la señal de la vela anterior a la apertura de esta vela
            if pending_signal is not None:
                trade = self._open_trade(pending_signal, o, bar_time, balance, pair, pip_size, half_spread, slip)
                if trade is not None:
                    active_trade = trade
                pending_signal = None

            # 2. Monitorear posición activa (incluida la recién abierta: el rango de
            #    la vela ocurre después de la apertura)
            if active_trade is not None:
                exit_price, reason = self._check_exit(active_trade, o, h, l, half_spread, slip)
                if reason is not None:
                    close_trade(active_trade, exit_price, bar_time, reason)
                    active_trade = None

            # 3. Equity al cierre (flotante valuada al precio al que se cerraría)
            floating = 0.0
            if active_trade is not None:
                if active_trade["side"] == OrderSide.BUY:
                    floating = (c - half_spread - active_trade["entry_price"]) * active_trade["units"]
                else:
                    floating = (active_trade["entry_price"] - (c + half_spread)) * active_trade["units"]
                floating *= quote_to_usd_rate(pair, c)
            equity = balance + floating
            equity_records[bar_time] = equity

            # 4. Límite de pérdida diaria (como el circuit breaker en vivo)
            daily_blocked = False
            if self.max_daily_loss_pct is not None and day_start_equity > 0:
                daily_loss_pct = max(0.0, day_start_equity - equity) / day_start_equity * 100.0
                if daily_loss_pct >= self.max_daily_loss_pct:
                    daily_blocked = True
                    blocked_days.add(current_day)
            daily_blocked = daily_blocked or current_day in blocked_days

            # 5. Evaluar nueva señal con la vela i cerrada; se ejecuta en i+1
            if active_trade is None and not daily_blocked and balance > 100 and i + 1 < len(df):
                hist_slice = df.iloc[max(0, i + 1 - strategy.lookback_window): i + 1]
                sig = strategy.evaluate(hist_slice, pair=pair)
                if sig and sig.direction in (SignalDirection.BUY, SignalDirection.SELL):
                    pending_signal = sig

        # Cerrar posición residual al final del periodo si quedó abierta
        if active_trade is not None:
            c = float(df["close"].iloc[-1])
            if active_trade["side"] == OrderSide.BUY:
                exit_price = c - half_spread - slip
            else:
                exit_price = c + half_spread + slip
            close_trade(active_trade, exit_price, df.index[-1], "end_of_data")
            equity_records[df.index[-1]] = balance

        equity_series = pd.Series(equity_records, name="Equity")
        metrics = calculate_metrics(trades, equity_series, initial_balance=self.initial_balance)
        trades_df = pd.DataFrame(trades)

        return BacktestResult(
            strategy_name=strategy.name,
            pair=pair,
            metrics=metrics,
            trades=trades,
            equity_series=equity_series,
            trades_df=trades_df,
        )

    def trade_pnl(self, trade: Dict[str, Any], exit_price: float, pair: str, exit_time=None):
        """(pnl neto en USD, comisión, diferencia de precio a favor) al cerrar `trade` en `exit_price`.
        Con `exit_time` también descuenta el swap (si financing_annual_pct > 0)."""
        is_buy = trade["side"] == OrderSide.BUY
        diff = (exit_price - trade["entry_price"]) if is_buy else (trade["entry_price"] - exit_price)
        gross = diff * trade["units"] * quote_to_usd_rate(pair, exit_price)  # en USD
        comm = trade["units"] / contract_size(pair) * self.commission_per_lot
        return gross - comm - self.swap_usd(trade, exit_time, pair), comm, diff

    def swap_usd(self, trade: Dict[str, Any], exit_time, pair: str) -> float:
        if exit_time is None or not self.financing_annual_pct:
            return 0.0
        days = (pd.Timestamp(exit_time) - pd.Timestamp(trade["entry_time"])).total_seconds() / 86400.0
        return financing_cost_usd(pair, trade["units"], trade["entry_price"], days, self.financing_annual_pct)

    def _open_trade(
        self,
        sig: Signal,
        open_price: float,
        bar_time,
        balance: float,
        pair: str,
        pip_size: float,
        half_spread: float,
        slip: float,
    ) -> Optional[Dict[str, Any]]:
        side = OrderSide.BUY if sig.direction == SignalDirection.BUY else OrderSide.SELL
        is_buy = side == OrderSide.BUY
        entry_price = open_price + half_spread + slip if is_buy else open_price - half_spread - slip

        # Distancias pedidas por la estrategia, medidas desde su precio de referencia
        ref = sig.entry_price or open_price
        sl_dist = abs(ref - sig.stop_loss) if sig.stop_loss else self.default_sl_pips * pip_size
        tp_dist = abs(sig.take_profit - ref) if sig.take_profit else self.default_tp_pips * pip_size
        sl = entry_price - sl_dist if is_buy else entry_price + sl_dist
        tp = entry_price + tp_dist if is_buy else entry_price - tp_dist

        units = calculate_position_size(
            account_balance=balance,
            risk_pct=self.risk_per_trade_pct,
            entry_price=entry_price,
            stop_loss_price=sl,
            pair=pair,
        )
        if units <= 0:
            return None
        return {
            "side": side,
            "units": units,
            "risk_usd": sl_dist * units * quote_to_usd_rate(pair, entry_price),
            "entry_price": entry_price,
            "signal_time": sig.timestamp,
            "entry_time": bar_time,
            "stop_loss": sl,
            "take_profit": tp,
        }

    @staticmethod
    def _check_exit(trade: Dict[str, Any], o: float, h: float, l: float, half_spread: float, slip: float):
        """
        Retorna (precio_salida, motivo) o (None, None). Un BUY cierra al bid
        (medio - medio spread) y un SELL al ask (medio + medio spread). Si la
        apertura ya salta el SL (gap), se llena a la apertura. SL tiene prioridad.
        """
        sl, tp = trade["stop_loss"], trade["take_profit"]
        if trade["side"] == OrderSide.BUY:
            bid_o, bid_h, bid_l = o - half_spread, h - half_spread, l - half_spread
            if bid_l <= sl:
                return min(sl, bid_o) - slip, "sl"
            if bid_h >= tp:
                return tp - slip, "tp"
        else:
            ask_o, ask_h, ask_l = o + half_spread, h + half_spread, l + half_spread
            if ask_h >= sl:
                return max(sl, ask_o) + slip, "sl"
            if ask_l <= tp:
                return tp + slip, "tp"
        return None, None
