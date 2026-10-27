"""
Cargador de datos históricos para Backtesting y Análisis Cuantitativo.
Soporta Dukascopy (fuente principal, con caché parquet en data/), MT5,
TimescaleDB, archivos CSV y generación de datos sintéticos.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd

try:
    import psycopg2
except ImportError:
    psycopg2 = None

from shared.config import settings
from shared.dukascopy import DUKASCOPY_INTERVALS, fetch_mid_candles, only_closed
from shared.fx import normalize_pair
from shared.logger import get_logger

log = get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def dukascopy_cache_path(pair: str, granularity: str, data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / f"dukascopy_{normalize_pair(pair)}_{granularity.upper()}.parquet"


def load_from_dukascopy(
    pair: str = "EUR_USD",
    granularity: str = "H1",
    years: float = 6.0,
    refresh: bool = True,
    data_dir: Path = DATA_DIR,
    now: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Velas de precio medio de Dukascopy con caché en data/ (parquet).

    La primera vez descarga `years` años; después solo baja lo que falta desde
    la última vela guardada (refresh=False usa la caché sin conectarse).
    Solo devuelve velas CERRADAS: la vela en formación nunca se guarda.
    """
    granularity = granularity.upper()
    if granularity not in DUKASCOPY_INTERVALS:
        raise ValueError(f"Granularidad '{granularity}' no soportada por Dukascopy: {sorted(DUKASCOPY_INTERVALS)}")
    now = now or datetime.now(timezone.utc)
    path = dukascopy_cache_path(pair, granularity, data_dir)

    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    if not refresh:
        return cached

    wanted_start = now - timedelta(days=365.25 * years)
    if cached.empty:
        fresh = fetch_mid_candles(pair, granularity, wanted_start, now)
    else:
        # Se vuelve a pedir la última vela guardada por si quedó a medio consolidar.
        fresh = fetch_mid_candles(pair, granularity, cached.index[-1].to_pydatetime(), now)
        # Si ahora se piden más años que los guardados, se completa la historia hacia atrás.
        if cached.index[0].to_pydatetime() - wanted_start > timedelta(days=7):
            older = fetch_mid_candles(pair, granularity, wanted_start, cached.index[0].to_pydatetime())
            fresh = pd.concat([older, fresh])

    df = pd.concat([cached, fresh]) if not cached.empty else fresh
    if df.empty:
        return df
    df = only_closed(df[~df.index.duplicated(keep="last")].sort_index(), granularity, now)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    log.info(f"Caché {path.name}: {len(df)} velas [{df.index[0]} a {df.index[-1]}]")
    return df


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


def load_from_mt5(pair: str = "EUR_USD", granularity: str = "M5", n_candles: int = 50000) -> pd.DataFrame:
    """
    Descarga historial real directamente desde el terminal MT5 (p.ej. tu cuenta XM).
    Requiere Windows con el terminal abierto. Timestamps en UTC, solo velas cerradas.
    """
    from shared.mt5_client import MT5Client

    client = MT5Client()
    if not client.connect():
        return pd.DataFrame()
    try:
        candles = client.get_latest_candles(pair=pair, granularity=granularity, count=n_candles)
    finally:
        client.disconnect()
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame([c.model_dump() for c in candles])
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].set_index("timestamp")
    df.index = pd.to_datetime(df.index, utc=True)
    df["volume"] = df["volume"].astype(float)
    log.info(f"Descargadas {len(df)} velas de {pair} {granularity} desde MT5.")
    return df


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
