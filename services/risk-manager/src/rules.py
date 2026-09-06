"""
Motor de reglas de riesgo (Risk Gatekeeper).
Aplica filtros estrictos para autorizar o bloquear órdenes antes de su ejecución.
"""
from datetime import datetime, timezone, date
from typing import Optional, Tuple

try:
    import psycopg2
except ImportError:
    psycopg2 = None

from shared.config import settings
from shared.logger import get_logger
from shared.models import Signal, RiskAssessment, SignalDirection
from position_sizing import calculate_position_size, get_pip_size

log = get_logger(__name__)


class RiskRulesEngine:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or settings.postgres_dsn
        self._conn = None

    def _get_connection(self):
        if psycopg2 is None:
            return None
        if self._conn is None or self._conn.closed:
            try:
                self._conn = psycopg2.connect(self.dsn)
                self._conn.autocommit = True
            except Exception as e:
                log.debug(f"Conexión a BD no disponible: {e}")
                self._conn = None
        return self._conn

    def get_open_positions_count(self) -> int:
        """Retorna el número actual de órdenes/posiciones abiertas."""
        conn = self._get_connection()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM orders WHERE status IN ('pending', 'open', 'filled');")
                res = cur.fetchone()
                return res[0] if res else 0
        except Exception as e:
            log.warning(f"Error consultando posiciones abiertas en BD: {e}")
            return 0

    def get_today_realized_pnl(self) -> Tuple[float, float]:
        """
        Retorna (pnl_usd, starting_equity_usd) del día actual para control de Drawdown.
        """
        conn = self._get_connection()
        if not conn:
            return 0.0, 10000.0
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(SUM(pnl), 0) FROM orders WHERE status = 'closed' AND closed_at >= %s;",
                    (today_start,),
                )
                res = cur.fetchone()
                pnl = float(res[0]) if res else 0.0
                return pnl, 10000.0  # Equity estimada base
        except Exception as e:
            log.warning(f"Error consultando PnL diario en BD: {e}")
            return 0.0, 10000.0

    def is_pair_already_open(self, pair: str) -> bool:
        """Evita duplicar órdenes sobre el mismo par si ya hay una activa."""
        conn = self._get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM orders WHERE pair = %s AND status IN ('pending', 'open', 'filled');",
                    (pair,),
                )
                res = cur.fetchone()
                return (res[0] > 0) if res else False
        except Exception:
            return False

    def evaluate_signal(self, signal: Signal, account_balance: float = 10000.0) -> RiskAssessment:
        """
        Evalúa integralmente una señal de trading contra todas las reglas de gestión de riesgo.
        """
        if signal.direction not in (SignalDirection.BUY, SignalDirection.SELL):
            return RiskAssessment(approved=False, reason=f"Dirección de señal no ejecutable: {signal.direction}")

        # 1. Regla: Balance Mínimo
        if account_balance <= 100.0:
            return RiskAssessment(approved=False, reason="Balance insuficiente para operar (< $100)")

        # 2. Regla: Límite de Posiciones Abiertas Simultáneas
        open_count = self.get_open_positions_count()
        if open_count >= settings.max_open_positions:
            return RiskAssessment(
                approved=False,
                reason=f"Límite de posiciones abiertas alcanzado ({open_count}/{settings.max_open_positions})",
                account_balance=account_balance,
            )

        # 3. Regla: Control de Duplicados en el mismo par
        if self.is_pair_already_open(signal.pair):
            return RiskAssessment(
                approved=False,
                reason=f"Ya existe una posición abierta activa para el par {signal.pair}",
                account_balance=account_balance,
            )

        # 4. Regla: Circuit Breaker de Drawdown Diario Máximo
        today_pnl, start_eq = self.get_today_realized_pnl()
        if start_eq > 0:
            daily_loss_pct = abs(min(0.0, today_pnl)) / start_eq * 100.0
            if daily_loss_pct >= settings.max_daily_drawdown_pct:
                return RiskAssessment(
                    approved=False,
                    reason=f"🚨 CIRCUIT BREAKER: Pérdida diaria máxima alcanzada ({daily_loss_pct:.2f}% >= {settings.max_daily_drawdown_pct}%)",
                    account_balance=account_balance,
                )

        # 5. Validación y asignación de Stop Loss / Take Profit
        entry_price = signal.entry_price or 1.0
        pip_size = get_pip_size(signal.pair)

        sl_price = signal.stop_loss
        if sl_price is None:
            # Asignar Stop Loss por defecto si no venía en la señal
            sl_distance = settings.default_stop_loss_pips * pip_size
            sl_price = round(entry_price - sl_distance if signal.direction == SignalDirection.BUY else entry_price + sl_distance, 5)

        tp_price = signal.take_profit
        if tp_price is None:
            # Asignar Take Profit por defecto (según min risk/reward ratio)
            tp_distance = settings.default_take_profit_pips * pip_size
            tp_price = round(entry_price + tp_distance if signal.direction == SignalDirection.BUY else entry_price - tp_distance, 5)

        # 6. Regla: Ratio Riesgo / Beneficio Mínimo
        risk_dist = abs(entry_price - sl_price)
        reward_dist = abs(tp_price - entry_price)
        if risk_dist > 0:
            rr_ratio = reward_dist / risk_dist
            if rr_ratio < (settings.min_risk_reward_ratio - 0.05):
                return RiskAssessment(
                    approved=False,
                    reason=f"Ratio Riesgo/Beneficio insuficiente ({rr_ratio:.2f} < {settings.min_risk_reward_ratio})",
                    account_balance=account_balance,
                )

        # 7. Cálculo de Dimensionamiento de Posición (Units)
        units = calculate_position_size(
            account_balance=account_balance,
            risk_pct=settings.risk_per_trade_pct,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            pair=signal.pair,
        )

        if units <= 0:
            return RiskAssessment(
                approved=False,
                reason="Tamaño de posición calculado es 0 o inválido.",
                account_balance=account_balance,
            )

        risk_amount = account_balance * (settings.risk_per_trade_pct / 100.0)

        return RiskAssessment(
            approved=True,
            reason="APROBADO por Risk Manager",
            recommended_units=units,
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_amount_usd=risk_amount,
            risk_percent=settings.risk_per_trade_pct,
            account_balance=account_balance,
        )

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
