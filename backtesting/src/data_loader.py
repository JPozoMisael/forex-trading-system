"""
Cargador de datos históricos para Backtesting y Análisis Cuantitativo.
Soporta TimescaleDB, archivos CSV y generación de datos sintéticos realistas.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import numpy as np
import pandas as pd

try:
    import psycopg2
except ImportError:
    psycopg2 = None

from shared.config import settings
from shared.logger import get_logger

log = get_logger(__name__)


def load_from_db(
    pair: str = "EUR_USD",
    granularity: str = "M5",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 5000,
) -> pd.DataFrame:
    """Carga velas históricas directamente desde TimescaleDB."""
    if psycopg2 is None:
        log.debug("psycopg2 no disponible en el entorno local.")
        return pd.DataFrame()

    try:
        conn = psycopg2.connect(settings.postgres_dsn)
        query = """
            SELECT timestamp, open, high, low, close, volume
            FROM candles
            WHERE pair = %s AND granularity = %s
        """
        params = [pair, granularity]
        if start_date:
            query += " AND timestamp >= %s"
            params.append(start_date)
        if end_date:
            query += " AND timestamp <= %s"
            params.append(end_date)

        query += " ORDER BY timestamp ASC LIMIT %s;"
        params.append(limit)

        df = pd.read_sql_query(query, conn, params=tuple(params))
        conn.close()

        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.set_index("timestamp", inplace=True)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            log.info(f"Cargadas {len(df)} velas de {pair} desde TimescaleDB.")
            return df
    except Exception as e:
        log.warning(f"No se pudieron cargar velas desde BD ({e}).")

    return pd.DataFrame()


def load_from_csv(csv_path: str) -> pd.DataFrame:
    """Carga velas desde un archivo CSV."""
    df = pd.read_csv(csv_path)
    df.columns = [c.lower().strip() for c in df.columns]

    date_col = next((c for c in df.columns if c in ("timestamp", "time", "date", "datetime")), None)
    if date_col:
        df["timestamp"] = pd.to_datetime(df[date_col])
        df.set_index("timestamp", inplace=True)
        if date_col != "timestamp":
            df.drop(columns=[date_col], inplace=True)

    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            df[col] = df[col].astype(float)

    if "volume" in df.columns:
        df["volume"] = df["volume"].astype(float)
    else:
        df["volume"] = 100.0

    df.sort_index(inplace=True)
    return df


def generate_synthetic_forex_data(
    pair: str = "EUR_USD",
    n_candles: int = 1500,
    granularity_minutes: int = 5,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Genera una serie de tiempo sintética con propiedades realistas de Forex:
    volatilidad estocástica, saltos y tendencias.
    """
    np.random.seed(seed)
    base_price = 155.0 if "JPY" in pair else 1.0850
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(minutes=n_candles * granularity_minutes)

    timestamps = [start_time + timedelta(minutes=i * granularity_minutes) for i in range(n_candles)]

    dt = 1 / (252 * (1440 / granularity_minutes))
    volatility = 0.08
    drift = 0.01

    returns = np.random.normal((drift - 0.5 * volatility**2) * dt, volatility * np.sqrt(dt), n_candles)
    trend = np.sin(np.linspace(0, 6 * np.pi, n_candles)) * 0.0003
    returns += trend

    price_path = base_price * np.exp(np.cumsum(returns))

    opens = np.zeros(n_candles)
    highs = np.zeros(n_candles)
    lows = np.zeros(n_candles)
    closes = np.zeros(n_candles)
    volumes = np.random.randint(50, 1000, n_candles)

    opens[0] = base_price
    closes[0] = price_path[0]
    highs[0] = max(opens[0], closes[0]) + abs(np.random.normal(0, base_price * 0.0002))
    lows[0] = min(opens[0], closes[0]) - abs(np.random.normal(0, base_price * 0.0002))

    for i in range(1, n_candles):
        opens[i] = closes[i - 1]
        closes[i] = price_path[i]
        intraday_noise = abs(np.random.normal(0, base_price * 0.0003))
        highs[i] = max(opens[i], closes[i]) + intraday_noise
        lows[i] = min(opens[i], closes[i]) - intraday_noise

    df = pd.DataFrame(
        {
            "open": np.round(opens, 5),
            "high": np.round(highs, 5),
            "low": np.round(lows, 5),
            "close": np.round(closes, 5),
            "volume": volumes,
        },
        index=pd.to_datetime(timestamps),
    )
    df.index.name = "timestamp"
    return df
