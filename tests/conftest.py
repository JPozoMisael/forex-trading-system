"""
Configuración común y fixtures para pytest.
"""
import sys
import os
from datetime import datetime, timezone, timedelta
import pytest
import pandas as pd
import numpy as np

# Inyectar rutas de servicios y shared al sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "services/strategy-engine/src"))
sys.path.insert(0, os.path.join(BASE_DIR, "services/risk-manager/src"))
sys.path.insert(0, os.path.join(BASE_DIR, "services/execution-engine/src"))
sys.path.insert(0, os.path.join(BASE_DIR, "services/data-collector/src"))
sys.path.insert(0, os.path.join(BASE_DIR, "backtesting/src"))


@pytest.fixture
def sample_ohlcv_dataframe():
    """Genera un DataFrame OHLCV determinístico para pruebas."""
    n = 100
    dates = [datetime.now(timezone.utc) - timedelta(minutes=5 * (n - i)) for i in range(n)]
    np.random.seed(42)
    base = 1.0850
    returns = np.random.normal(0, 0.0005, n)
    closes = base + np.cumsum(returns)
    opens = np.roll(closes, 1)
    opens[0] = base
    highs = np.maximum(opens, closes) + 0.0003
    lows = np.minimum(opens, closes) - 0.0003
    volumes = np.random.randint(50, 500, n)

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=pd.to_datetime(dates),
    )
    df.index.name = "timestamp"
    return df
