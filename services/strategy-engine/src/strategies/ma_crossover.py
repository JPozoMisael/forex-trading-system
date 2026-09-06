"""
Estrategia de Cruce de Medias Móviles Exponenciales (EMA Crossover)
con filtro de tendencia y cálculo dinámico de Stop Loss / Take Profit con ATR.
"""
from datetime import datetime, timezone
from typing import Optional
import pandas as pd

from indicators import calculate_ema, calculate_atr
from shared.logger import get_logger
from shared.models import Signal, SignalDirection
from strategies.base_strategy import BaseStrategy

log = get_logger(__name__)


class MACrossoverStrategy(BaseStrategy):
    def __init__(
        self,
        fast_period: int = 9,
        slow_period: int = 21,
        trend_period: int = 200,
        use_trend_filter: bool = True,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
        atr_tp_multiplier: float = 3.0,
    ):
        super().__init__(name="EMA_Crossover")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.trend_period = trend_period
        self.use_trend_filter = use_trend_filter
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier
        self.atr_tp_multiplier = atr_tp_multiplier

    @property
    def min_candles_required(self) -> int:
        return max(self.trend_period if self.use_trend_filter else self.slow_period, self.atr_period) + 5

    def evaluate(self, df: pd.DataFrame, pair: str) -> Optional[Signal]:
        if not self.validate_dataframe(df):
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]

        ema_fast = calculate_ema(close, self.fast_period)
        ema_slow = calculate_ema(close, self.slow_period)
        atr = calculate_atr(high, low, close, self.atr_period)

        if self.use_trend_filter and len(df) >= self.trend_period:
            ema_trend = calculate_ema(close, self.trend_period)
        else:
            ema_trend = None

        # Condición de cruce en las últimas 2 velas cerradas (-2 y -1)
        prev_fast, curr_fast = ema_fast.iloc[-2], ema_fast.iloc[-1]
        prev_slow, curr_slow = ema_slow.iloc[-2], ema_slow.iloc[-1]
        curr_close = close.iloc[-1]
        curr_atr = atr.iloc[-1]

        # Si ATR es 0 o inválido, usamos un valor por defecto seguro
        if pd.isna(curr_atr) or curr_atr <= 0:
            curr_atr = curr_close * 0.0015

        ts = df.index[-1] if isinstance(df.index[-1], datetime) else datetime.now(timezone.utc)

        # Cruce Alcista (Golden Cross): Fast cruza hacia arriba a Slow
        bullish_crossover = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
        trend_bullish = (ema_trend is None) or (curr_close > ema_trend.iloc[-1])

        if bullish_crossover and trend_bullish:
            sl = round(curr_close - (curr_atr * self.atr_sl_multiplier), 5)
            tp = round(curr_close + (curr_atr * self.atr_tp_multiplier), 5)
            return Signal(
                pair=pair,
                strategy_name=self.name,
                direction=SignalDirection.BUY,
                confidence=0.85,
                timestamp=ts,
                entry_price=curr_close,
                stop_loss=sl,
                take_profit=tp,
                metadata={
                    "ema_fast": float(curr_fast),
                    "ema_slow": float(curr_slow),
                    "atr": float(curr_atr),
                    "risk_reward_ratio": round(self.atr_tp_multiplier / self.atr_sl_multiplier, 2),
                },
            )

        # Cruce Bajista (Death Cross): Fast cruza hacia abajo a Slow
        bearish_crossover = (prev_fast >= prev_slow) and (curr_fast < curr_slow)
        trend_bearish = (ema_trend is None) or (curr_close < ema_trend.iloc[-1])

        if bearish_crossover and trend_bearish:
            sl = round(curr_close + (curr_atr * self.atr_sl_multiplier), 5)
            tp = round(curr_close - (curr_atr * self.atr_tp_multiplier), 5)
            return Signal(
                pair=pair,
                strategy_name=self.name,
                direction=SignalDirection.SELL,
                confidence=0.85,
                timestamp=ts,
                entry_price=curr_close,
                stop_loss=sl,
                take_profit=tp,
                metadata={
                    "ema_fast": float(curr_fast),
                    "ema_slow": float(curr_slow),
                    "atr": float(curr_atr),
                    "risk_reward_ratio": round(self.atr_tp_multiplier / self.atr_sl_multiplier, 2),
                },
            )

        return None
