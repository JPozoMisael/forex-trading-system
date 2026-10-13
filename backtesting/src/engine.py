"""
Motor de Simulación de Backtesting orientada a eventos.
Simula con fidelidad spreads, deslizamiento (slippage), comisiones, apalancamiento y reglas de salida SL/TP.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

import sys
import os

# Permitir imports desde services y shared
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/strategy-engine/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/risk-manager/src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from shared.models import Signal, SignalDirection, OrderSide
from strategies.base_strategy import BaseStrategy
from position_sizing import calculate_position_size, get_pip_size
from shared.fx import quote_to_usd_rate
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
        commission_per_unit: float = 0.0,
    ):
        self.initial_balance = initial_balance
        self.risk_per_trade_pct = risk_per_trade_pct
        self.spread_pips = spread_pips
        self.slippage_pips = slippage_pips
        self.commission_per_unit = commission_per_unit

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
        spread_cost = self.spread_pips * pip_size
        slippage_cost = self.slippage_pips * pip_size

        balance = self.initial_balance
        equity_records = {}
        trades: List[Dict[str, Any]] = []

        active_trade: Optional[Dict[str, Any]] = None
        min_bars = strategy.min_candles_required

        for i in range(min_bars, len(df)):
            current_bar = df.iloc[i]
            bar_time = df.index[i]
            o, h, l, c = current_bar["open"], current_bar["high"], current_bar["low"], current_bar["close"]

            # 1. Monitorear posición activa existente
            if active_trade is not None:
                side = active_trade["side"]
                entry = active_trade["entry_price"]
                sl = active_trade["stop_loss"]
                tp = active_trade["take_profit"]
                units = active_trade["units"]

                closed = False
                exit_price = c
                exit_reason = "normal"

                if side == OrderSide.BUY:
                    if sl and l <= sl:
                        closed = True
                        exit_price = sl - slippage_cost
                        exit_reason = "sl"
                    elif tp and h >= tp:
                        closed = True
                        exit_price = tp
                        exit_reason = "tp"
                elif side == OrderSide.SELL:
                    if sl and h >= sl:
                        closed = True
                        exit_price = sl + slippage_cost
                        exit_reason = "sl"
                    elif tp and l <= tp:
                        closed = True
                        exit_price = tp
                        exit_reason = "tp"

                if closed:
                    diff = (exit_price - entry) if side == OrderSide.BUY else (entry - exit_price)
                    pnl = diff * units * quote_to_usd_rate(pair, exit_price)  # en USD
                    pips = diff / pip_size

                    comm = units * self.commission_per_unit
                    pnl -= comm
                    balance += pnl

                    trade_record = {
                        "trade_id": len(trades) + 1,
                        "pair": pair,
                        "strategy": strategy.name,
                        "side": side.value,
                        "units": units,
                        "entry_time": active_trade["entry_time"],
                        "entry_price": entry,
                        "exit_time": bar_time,
                        "exit_price": round(exit_price, 5),
                        "stop_loss": sl,
                        "take_profit": tp,
                        "pnl": round(pnl, 2),
                        "pips": round(pips, 1),
                        "roi_pct": round(pnl / active_trade["risk_usd"] * 100, 1) if active_trade["risk_usd"] else 0.0,
                        "exit_reason": exit_reason,
                        "balance_after": round(balance, 2),
                    }
                    trades.append(trade_record)
                    active_trade = None

            # 2. Evaluar nueva señal si no hay posición activa
            if active_trade is None and balance > 100:
                hist_slice = df.iloc[max(0, i + 1 - strategy.lookback_window): i + 1]
                sig = strategy.evaluate(hist_slice, pair=pair)

                if sig and sig.direction in (SignalDirection.BUY, SignalDirection.SELL):
                    side = OrderSide.BUY if sig.direction == SignalDirection.BUY else OrderSide.SELL
                    entry_price = c + (spread_cost / 2) + slippage_cost if side == OrderSide.BUY else c - (spread_cost / 2) - slippage_cost

                    sl = sig.stop_loss
                    tp = sig.take_profit

                    # Si la estrategia no calculó SL/TP, usamos distancias por defecto
                    if sl is None:
                        sl = entry_price - (20 * pip_size) if side == OrderSide.BUY else entry_price + (20 * pip_size)
                    if tp is None:
                        tp = entry_price + (40 * pip_size) if side == OrderSide.BUY else entry_price - (40 * pip_size)

                    units = calculate_position_size(
                        account_balance=balance,
                        risk_pct=self.risk_per_trade_pct,
                        entry_price=entry_price,
                        stop_loss_price=sl,
                        pair=pair,
                    )

                    if units > 0:
                        active_trade = {
                            "side": side,
                            "units": units,
                            "risk_usd": abs(entry_price - sl) * units * quote_to_usd_rate(pair, entry_price),
                            "entry_price": round(entry_price, 5),
                            "entry_time": bar_time,
                            "stop_loss": sl,
                            "take_profit": tp,
                        }

            # 3. Registrar equity flotante
            current_floating_pnl = 0.0
            if active_trade is not None:
                if active_trade["side"] == OrderSide.BUY:
                    current_floating_pnl = (c - active_trade["entry_price"]) * active_trade["units"]
                else:
                    current_floating_pnl = (active_trade["entry_price"] - c) * active_trade["units"]
                current_floating_pnl *= quote_to_usd_rate(pair, c)

            equity_records[bar_time] = balance + current_floating_pnl

        # Cerrar posición residual al final del periodo si quedó abierta
        if active_trade is not None:
            c = df.iloc[-1]["close"]
            diff = (c - active_trade["entry_price"]) if active_trade["side"] == OrderSide.BUY else (active_trade["entry_price"] - c)
            pnl = diff * active_trade["units"] * quote_to_usd_rate(pair, c)
            pips = diff / pip_size

            balance += pnl
            trades.append(
                {
                    "trade_id": len(trades) + 1,
                    "pair": pair,
                    "strategy": strategy.name,
                    "side": active_trade["side"].value,
                    "units": active_trade["units"],
                    "entry_time": active_trade["entry_time"],
                    "entry_price": active_trade["entry_price"],
                    "exit_time": df.index[-1],
                    "exit_price": round(c, 5),
                    "stop_loss": active_trade["stop_loss"],
                    "take_profit": active_trade["take_profit"],
                    "pnl": round(pnl, 2),
                    "pips": round(pips, 1),
                    "roi_pct": 0.0,
                    "exit_reason": "end_of_data",
                    "balance_after": round(balance, 2),
                }
            )

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
