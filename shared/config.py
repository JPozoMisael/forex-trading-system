"""
Carga centralizada de configuración desde variables de entorno.
Todos los servicios importan este módulo para no duplicar lógica de .env
"""
import os
from dataclasses import dataclass, field
from typing import List


def _get_list(env_var: str, default: str = "") -> List[str]:
    raw = os.getenv(env_var, default)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # OANDA
    oanda_api_key: str = os.getenv("OANDA_API_KEY", "")
    oanda_account_id: str = os.getenv("OANDA_ACCOUNT_ID", "")
    oanda_environment: str = os.getenv("OANDA_ENVIRONMENT", "practice")  # practice | live

    # Mercado
    forex_pairs: List[str] = field(default_factory=lambda: _get_list("FOREX_PAIRS", "EUR_USD,USD_JPY,GBP_USD,AUD_USD"))
    candle_granularity: str = os.getenv("CANDLE_GRANULARITY", "M5")
    collector_poll_seconds: int = int(os.getenv("COLLECTOR_POLL_SECONDS", "30"))

    # Base de datos (TimescaleDB / PostgreSQL)
    postgres_user: str = os.getenv("POSTGRES_USER", "forex")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "forex_pw")
    postgres_db: str = os.getenv("POSTGRES_DB", "forex_data")
    postgres_host: str = os.getenv("POSTGRES_HOST", "timescaledb")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))

    # Redis
    redis_host: str = os.getenv("REDIS_HOST", "redis")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))

    # Gestión de Riesgo (Risk Manager)
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))  # % del balance por trade
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
    max_daily_drawdown_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "5.0"))  # Kill-switch
    max_spread_pips: float = float(os.getenv("MAX_SPREAD_PIPS", "3.0"))  # Filtro de spread
    default_stop_loss_pips: float = float(os.getenv("DEFAULT_STOP_LOSS_PIPS", "20.0"))
    default_take_profit_pips: float = float(os.getenv("DEFAULT_TAKE_PROFIT_PIPS", "40.0"))
    min_risk_reward_ratio: float = float(os.getenv("MIN_RISK_REWARD_RATIO", "1.5"))

    # Modo de Ejecución (dry_run permite simular órdenes sin cuenta real/oanda)
    dry_run: bool = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

    # Proveedor de datos de mercado activo. Por ahora solo "mt5" (pruebas locales en
    # Windows con la cuenta demo). El soporte de "oanda" quedó comentado en
    # build_market_client() hasta que se decida un broker definitivo con API REST.
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "mt5").lower()

    # MT5 (solo aplica si market_data_provider="mt5"; requiere terminal MT5 en Windows)
    mt5_login: int = int(os.getenv("MT5_LOGIN", "0") or "0")
    mt5_password: str = os.getenv("MT5_PASSWORD", "")
    mt5_server: str = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
    mt5_timeout: int = int(os.getenv("MT5_TIMEOUT", "60000"))
    default_volume: float = float(os.getenv("DEFAULT_VOLUME", "0.01"))
    slippage: int = int(os.getenv("SLIPPAGE", "10"))

    # Alertas Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    enable_telegram_signals: bool = os.getenv("ENABLE_TELEGRAM_SIGNALS", "true").lower() in ("true", "1", "yes")
    enable_telegram_orders: bool = os.getenv("ENABLE_TELEGRAM_ORDERS", "true").lower() in ("true", "1", "yes")
    enable_telegram_trades: bool = os.getenv("ENABLE_TELEGRAM_TRADES", "true").lower() in ("true", "1", "yes")

    # Alertas por Email (SMTP, ej. Gmail con "contraseña de aplicación")
    email_smtp_host: str = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    email_smtp_port: int = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    email_sender: str = os.getenv("EMAIL_SENDER", "")
    email_app_password: str = os.getenv("EMAIL_APP_PASSWORD", "")
    email_recipient: str = os.getenv("EMAIL_RECIPIENT", "")
    enable_email_signals: bool = os.getenv("ENABLE_EMAIL_SIGNALS", "true").lower() in ("true", "1", "yes")
    enable_email_orders: bool = os.getenv("ENABLE_EMAIL_ORDERS", "true").lower() in ("true", "1", "yes")
    enable_email_trades: bool = os.getenv("ENABLE_EMAIL_TRADES", "true").lower() in ("true", "1", "yes")

    # Canales de Redis Pub/Sub
    channel_market_candle: str = "market:candle"          # Publica nueva vela cerrada
    channel_signals_new: str = "signals:new"              # Publica señal de estrategia
    channel_orders_approved: str = "orders:approved"      # Orden aprobada por Risk Manager
    channel_orders_executed: str = "orders:executed"      # Orden ejecutada en broker
    channel_orders_closed: str = "orders:closed"          # Orden/posición cerrada
    channel_risk_alerts: str = "risk:alerts"              # Alerta de violación de riesgo

    

    # General
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    environment: str = os.getenv("ENVIRONMENT", "development")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()