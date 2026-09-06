"""
Estrategia de Reversión a la Media basada en Bandas de Bollinger y oscilador RSI.
Detecta condiciones extremas de sobrecompra/sobreventa para capturar rebotes a la media.
"""
from datetime import datetime, timezone
from typing import Optional
import pandas as pd

from indicators import calculate_bollinger_bands, calculate_rsi, calculate_atr
from shared.logger import get_logger
from shared.models import Signal, SignalDirection
from strategies.base_strategy import BaseStrategy

log = get_logger(__name__)


class MeanReversionStrategy(BaseStrategy):
    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
    ):
        super().__init__(name="Bollinger_RSI_MeanReversion")
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier

    @property
    def min_candles_required(self) -> int:
        return max(self.bb_period, self.rsi_period, self.atr_period) + 5

    def evaluate(self, df: pd.DataFrame, pair: str) -> Optional[Signal]:
        if not self.validate_dataframe(df):
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]

        upper_bb, mid_bb, lower_bb = calculate_bollinger_bands(close, self.bb_period, self.bb_std)
        rsi = calculate_rsi(close, self.rsi_period)
        atr = calculate_atr(high, low, close, self.atr_period)

        curr_close = close.iloc[-1]
        prev_close = close.iloc[-2]
        curr_rsi = rsi.iloc[-1]
        prev_rsi = rsi.iloc[-2]
        curr_lower = lower_bb.iloc[-1]
        curr_upper = upper_bb.iloc[-1]
        curr_mid = mid_bb.iloc[-1]
        curr_atr = atr.iloc[-1] if not pd.isna(atr.iloc[-1]) and atr.iloc[-1] > 0 else curr_close * 0.0015

        ts = df.index[-1] if isinstance(df.index[-1], datetime) else datetime.now(timezone.utc)

        # Señal de Compra por Reversión:
        # Precio tocó o perforó la banda inferior y RSI sale de sobreventa
        if (prev_close <= lower_bb.iloc[-2] or low.iloc[-1] <= curr_lower) and (curr_rsi < self.rsi_oversold + 5) and (curr_close > prev_close):
            sl = round(curr_close - (curr_atr * self.atr_sl_multiplier), 5)
            # TP objetivo hacia la media de Bollinger (SMA 20) o más allá
            tp = round(max(curr_mid, curr_close + (curr_atr * 2.0)), 5)
            return Signal(
                pair=pair,
                strategy_name=self.name,
                direction=SignalDirection.BUY,
                confidence=0.80,
                timestamp=ts,
                entry_price=curr_close,
                stop_loss=sl,
                take_profit=tp,
                metadata={
                    "rsi": float(curr_rsi),
                    "bb_lower": float(curr_lower),
                    "bb_mid": float(curr_mid),
                    "atr": float(curr_atr),
                },
            )

        # Señal de Venta por Reversión:
        # Precio tocó o perforó la banda superior y RSI sale de sobrecompra
        if (prev_close >= upper_bb.iloc[-2] or high.iloc[-1] >= curr_upper) and (curr_rsi > self.rsi_overbought - 5) and (curr_close < prev_close):
            sl = round(curr_close + (curr_atr * self.atr_sl_multiplier), 5)
            tp = round(min(curr_mid, curr_close - (curr_atr * 2.0)), 5)
            return Signal(
                pair=pair,
                strategy_name=self.name,
                direction=SignalDirection.SELL,
                confidence=0.80,
                timestamp=ts,
                entry_price=curr_close,
                stop_loss=sl,
                take_profit=tp,
                metadata={
                    "rsi": float(curr_rsi),
                    "bb_upper": float(curr_upper),
                    "bb_mid": float(curr_mid),
                    "atr": float(curr_atr),
                },
            )

        return None
