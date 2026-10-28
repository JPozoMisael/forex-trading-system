"""
Carga centralizada de configuración desde variables de entorno.
Todos los servicios importan este módulo para no duplicar lógica de .env
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Al correr los servicios nativos en Windows (obligatorio con MT5/XM) nadie
# inyecta las variables del .env, así que lo cargamos aquí. override=False:
# si Docker Compose ya definió una variable, esa tiene prioridad.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass


def _get_list(env_var: str, default: str = "") -> List[str]:
    raw = os.getenv(env_var, default)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _get_bool(env_var: str, default: str = "false") -> bool:
    return os.getenv(env_var, default).strip().lower() in ("true", "1", "yes")


# Minutos por vela para cada granularidad soportada.
GRANULARITY_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D": 1440, "D1": 1440, "W1": 10080,
}


@dataclass(frozen=True)
class Settings:
    # OANDA (solo como fuente de datos alternativa; la ejecución real es vía MT5)
    oanda_api_key: str = os.getenv("OANDA_API_KEY", "")
    oanda_account_id: str = os.getenv("OANDA_ACCOUNT_ID", "")
    oanda_environment: str = os.getenv("OANDA_ENVIRONMENT", "practice")  # practice | live

    # Mercado
    forex_pairs: List[str] = field(default_factory=lambda: _get_list("FOREX_PAIRS", "EUR_USD,USD_JPY,GBP_USD,AUD_USD"))
    candle_granularity: str = os.getenv("CANDLE_GRANULARITY", "M5").upper()
    collector_poll_seconds: int = int(os.getenv("COLLECTOR_POLL_SECONDS", "15"))
    # Velas a descargar al arrancar para que las estrategias (EMA 200) tengan historia.
    collector_backfill_candles: int = int(os.getenv("COLLECTOR_BACKFILL_CANDLES", "1000"))
    # Estrategias del strategy-engine: ml (modelo de config.yaml), ma_crossover, mean_reversion.
    # (_get_list pasa a mayúsculas, pensado para pares; los nombres de estrategia van en minúsculas)
    active_strategies: List[str] = field(default_factory=lambda: [s.lower() for s in _get_list("ACTIVE_STRATEGIES", "ml")])

    # Base de datos (TimescaleDB / PostgreSQL)
    postgres_user: str = os.getenv("POSTGRES_USER", "forex")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "forex_pw")
    postgres_db: str = os.getenv("POSTGRES_DB", "forex_data")
    postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))

    # Redis
    redis_host: str = os.getenv("REDIS_HOST", "localhost")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))
    redis_password: str = os.getenv("REDIS_PASSWORD", "")

    # Gestión de Riesgo (Risk Manager)
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))  # % del balance por trade
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
    max_daily_drawdown_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "5.0"))  # Kill-switch
    max_spread_pips: float = float(os.getenv("MAX_SPREAD_PIPS", "3.0"))  # Filtro de spread
    default_stop_loss_pips: float = float(os.getenv("DEFAULT_STOP_LOSS_PIPS", "20.0"))
    default_take_profit_pips: float = float(os.getenv("DEFAULT_TAKE_PROFIT_PIPS", "40.0"))
    min_risk_reward_ratio: float = float(os.getenv("MIN_RISK_REWARD_RATIO", "1.5"))
    # Antigüedad máxima del snapshot de cuenta publicado por el execution-engine.
    # Si es más viejo, el risk-manager rechaza todo (fail-closed).
    account_snapshot_max_age_seconds: int = int(os.getenv("ACCOUNT_SNAPSHOT_MAX_AGE_SECONDS", "60"))
    # Una orden aprobada que no se ejecutó en este tiempo se descarta (evita
    # ejecutar órdenes viejas al reiniciar un servicio).
    order_max_age_seconds: int = int(os.getenv("ORDER_MAX_AGE_SECONDS", "120"))

    # Modo de Ejecución: dry_run=true simula órdenes con datos reales sin tocar el broker.
    dry_run: bool = _get_bool("DRY_RUN", "true")
    # Candado extra: aunque DRY_RUN=false, el sistema se niega a operar una cuenta
    # REAL de MT5 si esto no está en true. Las cuentas demo no lo necesitan.
    allow_live_trading: bool = _get_bool("ALLOW_LIVE_TRADING", "false")
    # Balance inicial de la cuenta simulada (solo DRY_RUN).
    sim_initial_balance: float = float(os.getenv("SIM_INITIAL_BALANCE", "10000"))

    # Proveedor de datos y ejecución: "mt5" (XM y cualquier broker MT5) u "oanda" (solo datos).
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "mt5").lower()

    # MT5 (requiere el terminal MT5 de escritorio abierto en la misma máquina Windows)
    mt5_login: int = int(os.getenv("MT5_LOGIN", "0") or "0")
    mt5_password: str = os.getenv("MT5_PASSWORD", "")
    mt5_server: str = os.getenv("MT5_SERVER", "")
    mt5_path: str = os.getenv("MT5_PATH", "")  # ruta a terminal64.exe si tienes varios terminales
    mt5_timeout: int = int(os.getenv("MT5_TIMEOUT", "60000"))
    # Sufijo de símbolos del broker. XM: Standard ""; Micro "micro"; Ultra Low "#" (verifícalo
    # con scripts/check_mt5.py).
    mt5_symbol_suffix: str = os.getenv("MT5_SYMBOL_SUFFIX", "")
    # Diferencia horaria del servidor MT5 respecto a UTC. "auto" = convención
    # "cierre de Nueva York" (UTC+2 invierno / UTC+3 verano) que usa XM.
    mt5_server_utc_offset: str = os.getenv("MT5_SERVER_UTC_OFFSET", "auto")
    mt5_magic: int = int(os.getenv("MT5_MAGIC", "260113"))
    slippage: int = int(os.getenv("SLIPPAGE", "10"))  # desviación máxima en points

    # Alertas Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    enable_telegram_signals: bool = _get_bool("ENABLE_TELEGRAM_SIGNALS", "true")
    enable_telegram_orders: bool = _get_bool("ENABLE_TELEGRAM_ORDERS", "true")
    enable_telegram_trades: bool = _get_bool("ENABLE_TELEGRAM_TRADES", "true")

    # Alertas por Email (SMTP, ej. Gmail con "contraseña de aplicación")
    email_smtp_host: str = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    email_smtp_port: int = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    email_sender: str = os.getenv("EMAIL_SENDER", "")
    email_app_password: str = os.getenv("EMAIL_APP_PASSWORD", "")
    email_recipient: str = os.getenv("EMAIL_RECIPIENT", "")
    enable_email_signals: bool = _get_bool("ENABLE_EMAIL_SIGNALS", "true")
    enable_email_orders: bool = _get_bool("ENABLE_EMAIL_ORDERS", "true")
    enable_email_trades: bool = _get_bool("ENABLE_EMAIL_TRADES", "true")

    # Avisos de fallo (sistema caído, sin velas nuevas...) y reporte diario (lunes a viernes)
    enable_system_alerts: bool = _get_bool("ENABLE_SYSTEM_ALERTS", "true")
    enable_daily_report: bool = _get_bool("ENABLE_DAILY_REPORT", "true")
    # Horas UTC de los reportes (HH:MM separadas por coma). Por defecto, antes de
    # cada sesión: 06:30 = 1:30 en Ecuador (Londres) y 13:00 = 8:00 en Ecuador (Nueva York).
    report_times_utc: List[str] = field(default_factory=lambda: _get_list("REPORT_TIMES_UTC", "06:30,13:00"))

    # Streams de Redis (persistentes: un servicio caído no pierde mensajes)
    channel_market_candle: str = "market:candle"          # Nueva vela cerrada (sufijo :PAR)
    channel_signals_new: str = "signals:new"              # Señal de estrategia
    channel_orders_approved: str = "orders:approved"      # Orden aprobada por Risk Manager
    channel_orders_executed: str = "orders:executed"      # Orden ejecutada en broker
    channel_orders_closed: str = "orders:closed"          # Posición cerrada
    channel_risk_alerts: str = "risk:alerts"              # Alerta de riesgo / sistema

    # Claves de estado en Redis publicadas por el execution-engine
    key_account_snapshot: str = "state:account"
    key_symbol_spec_prefix: str = "state:symbol:"
    # Última vela evaluada por el strategy-engine y lo que vio cada estrategia (sufijo PAR)
    key_strategy_state_prefix: str = "state:strategy:"
    key_daily_report_sent: str = "state:daily_report:last_sent"

    # General
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    environment: str = os.getenv("ENVIRONMENT", "development")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def granularity_minutes(self) -> int:
        return GRANULARITY_MINUTES.get(self.candle_granularity, 5)

    @property
    def executes_on_broker(self) -> bool:
        """True si las órdenes van al broker real (cuenta demo o real de MT5)."""
        return self.market_data_provider == "mt5" and not self.dry_run


settings = Settings()

# config.yaml: parámetros del modelo, backtest, validación y paper trading
# (sin secretos; las claves siguen viniendo del .env vía Settings).
CONFIG_YAML_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_trading_config(path: Optional[Path] = None) -> Dict[str, Any]:
    import yaml

    with open(path or CONFIG_YAML_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def instrument_config(cfg: Dict[str, Any], pair: str, granularity: Optional[str] = None) -> Dict[str, Any]:
    """
    config.yaml para un instrumento: `mercado.par` (y la temporalidad si se da) y
    sus costes de shared/fx.py (INSTRUMENT_COSTS). El resto (modelo, riesgo,
    validación) es el mismo para todos.
    """
    import copy

    from shared.fx import instrument_costs, normalize_pair

    out = copy.deepcopy(cfg)
    out["mercado"]["par"] = normalize_pair(pair)
    if granularity:
        out["mercado"]["temporalidad"] = granularity.upper()
    out["costes"].update(instrument_costs(pair))
    return out
