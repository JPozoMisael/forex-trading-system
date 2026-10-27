"""
Clase base abstracta para todas las estrategias de trading algorítmico.
Define la interfaz uniforme requerida para operar en vivo o en backtesting.
"""
from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd

from shared.models import Signal


class BaseStrategy(ABC):
    def __init__(self, name: str, params: Optional[dict] = None):
        self.name = name
        self.params = params or {}

    @property
    @abstractmethod
    def min_candles_required(self) -> int:
        """Número mínimo de velas necesarias para calcular indicadores confiables."""
        pass

    @property
    def lookback_window(self) -> int:
        """
        Velas de historia que recibe evaluate(), igual en vivo y en backtest.
        3x el mínimo para que las EMAs largas converjan (una EMA 200 calculada
        sobre solo 205 velas difiere bastante de la del gráfico).
        """
        return self.min_candles_required * 3

    def supports(self, pair: str, granularity: str) -> bool:
        """Si la estrategia sabe operar este par y temporalidad (las de reglas: todos)."""
        return True

    @abstractmethod
    def evaluate(self, df: pd.DataFrame, pair: str) -> Optional[Signal]:
        """
        Evalúa el DataFrame histórico de velas (con columnas open, high, low, close, volume)
        y retorna una señal de compra/venta/cierre o None si no hay señal.
        """
        pass

    def validate_dataframe(self, df: pd.DataFrame) -> bool:
        if df is None or len(df) < self.min_candles_required:
            return False
        required_cols = {"open", "high", "low", "close"}
        return required_cols.issubset(set(df.columns))
