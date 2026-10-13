"""
Motor de reglas de riesgo (Risk Gatekeeper).
Aplica filtros estrictos para autorizar o bloquear órdenes antes de su ejecución.

Principio FAIL-CLOSED: si falta información (BD caída, snapshot de cuenta viejo,
especificación del símbolo desconocida) la señal se RECHAZA. Un gestor de
riesgo que aprueba cuando no sabe no es un gestor de riesgo.
"""
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

import psycopg2
from psycopg2.extras import Json

from shared.bus import EventBus
from shared.config import settings
from shared.fx import get_pip_size
from shared.logger import get_logger
from shared.models import (
    ACTIVE_ORDER_STATUSES,
    AccountSummary,
    OrderSide,
    RiskAssessment,
    Signal,
    SignalDirection,
    SymbolSpec,
)
from position_sizing import calculate_lots

log = get_logger(__name__)


def _reject(reason: str, account: Optional[AccountSummary] = None) -> RiskAssessment:
    return RiskAssessment(approved=False, reason=reason, account_balance=account.balance if account else 0.0)


def evaluate_signal(
    signal: Signal,
    account: Optional[AccountSummary],
    spec: Optional[SymbolSpec],
    active_pairs: Iterable[str],
    now: Optional[datetime] = None,
) -> RiskAssessment:
    """
    Evalúa una señal contra todas las reglas de riesgo. Función pura (sin I/O)
    para poder probarla de forma aislada.
    """
    now = now or datetime.now(timezone.utc)
    active_pairs = list(active_pairs)

    if signal.direction not in (SignalDirection.BUY, SignalDirection.SELL):
        return _reject(f"Dirección de señal no ejecutable: {signal.direction}")
    if not signal.entry_price or signal.entry_price <= 0:
        return _reject("La señal no trae precio de entrada")

    # 1. Señal vigente: no ejecutar señales viejas (p.ej. tras un reinicio)
    max_age = timedelta(minutes=2 * settings.granularity_minutes, seconds=120)
    ts = signal.timestamp if signal.timestamp.tzinfo else signal.timestamp.replace(tzinfo=timezone.utc)
    if now - ts > max_age:
        return _reject(f"Señal expirada (vela de {ts:%H:%M} UTC)")

    # 2. Estado de cuenta real y reciente
    if account is None:
        return _reject("Sin snapshot de cuenta (¿execution-engine caído?)")
    acc_ts = account.timestamp if account.timestamp.tzinfo else account.timestamp.replace(tzinfo=timezone.utc)
    if (now - acc_ts).total_seconds() > settings.account_snapshot_max_age_seconds:
        return _reject(f"Snapshot de cuenta desactualizado ({int((now - acc_ts).total_seconds())}s)", account)
    if account.balance <= 100.0:
        return _reject("Balance insuficiente para operar (< 100)", account)

    # 3. Circuit Breaker de Drawdown Diario (sobre equity: incluye pérdidas flotantes)
    if not account.day_start_equity or account.day_start_equity <= 0:
        return _reject("Sin equity de inicio de día para el circuit breaker", account)
    daily_loss_pct = max(0.0, account.day_start_equity - account.equity) / account.day_start_equity * 100.0
    if daily_loss_pct >= settings.max_daily_drawdown_pct:
        return _reject(
            f"🚨 CIRCUIT BREAKER: Pérdida diaria máxima alcanzada ({daily_loss_pct:.2f}% >= {settings.max_daily_drawdown_pct}%)",
            account,
        )

    # 4. Límite de posiciones simultáneas y duplicados por par
    if len(active_pairs) >= settings.max_open_positions:
        return _reject(f"Límite de posiciones abiertas alcanzado ({len(active_pairs)}/{settings.max_open_positions})", account)
    if signal.pair in active_pairs:
        return _reject(f"Ya existe una posición activa para el par {signal.pair}", account)

    # 5. Especificación del símbolo y filtro de spread
    if spec is None:
        return _reject(f"Sin especificación del símbolo {signal.pair} (¿par no disponible en el broker?)", account)
    pip_size = get_pip_size(signal.pair)
    spread_pips = spec.spread_points * spec.point / pip_size
    if spread_pips > settings.max_spread_pips:
        return _reject(f"Spread demasiado alto ({spread_pips:.1f} > {settings.max_spread_pips} pips)", account)

    # 6. Stop Loss / Take Profit (por defecto si la estrategia no los trae) y lado correcto
    entry = signal.entry_price
    is_buy = signal.direction == SignalDirection.BUY
    sl = signal.stop_loss
    if sl is None:
        d = settings.default_stop_loss_pips * pip_size
        sl = entry - d if is_buy else entry + d
    tp = signal.take_profit
    if tp is None:
        d = settings.default_take_profit_pips * pip_size
        tp = entry + d if is_buy else entry - d
    if is_buy and not (sl < entry < tp):
        return _reject(f"SL/TP inconsistentes para BUY (SL={sl}, entrada={entry}, TP={tp})", account)
    if not is_buy and not (tp < entry < sl):
        return _reject(f"SL/TP inconsistentes para SELL (SL={sl}, entrada={entry}, TP={tp})", account)

    # 7. Ratio Riesgo / Beneficio Mínimo
    rr_ratio = abs(tp - entry) / abs(entry - sl)
    if rr_ratio < settings.min_risk_reward_ratio - 0.05:
        return _reject(f"Ratio Riesgo/Beneficio insuficiente ({rr_ratio:.2f} < {settings.min_risk_reward_ratio})", account)

    # 8. Dimensionamiento con el menor entre balance y equity (conservador)
    capital = min(account.balance, account.equity)
    lots = calculate_lots(capital, settings.risk_per_trade_pct, entry, sl, spec)
    if lots <= 0:
        return _reject("Tamaño de posición calculado es 0 (riesgo mínimo del broker excede el permitido)", account)

    risk_amount = (abs(entry - sl) / spec.tick_size) * spec.tick_value * lots
    return RiskAssessment(
        approved=True,
        reason="APROBADO por Risk Manager",
        recommended_units=int(round(lots * spec.contract_size)),
        recommended_lots=lots,
        stop_loss=round(sl, spec.digits),
        take_profit=round(tp, spec.digits),
        risk_amount_usd=round(risk_amount, 2),
        risk_percent=round(risk_amount / capital * 100.0, 3),
        account_balance=account.balance,
    )


class RiskRulesEngine:
    """Acceso a datos (BD y Redis) que alimenta a evaluate_signal()."""

    def __init__(self, dsn: Optional[str] = None, bus: Optional[EventBus] = None):
        self.dsn = dsn or settings.postgres_dsn
        self.bus = bus or EventBus()
        self._conn = None

    def _get_connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn, connect_timeout=5)
            self._conn.autocommit = True
        return self._conn

    def _reset_connection(self):
        self.close()

    def get_active_pairs(self) -> List[str]:
        """Pares con órdenes activas (reservadas, enviándose o abiertas). Lanza excepción si la BD falla."""
        try:
            with self._get_connection().cursor() as cur:
                cur.execute("SELECT pair FROM orders WHERE status = ANY(%s);", (list(ACTIVE_ORDER_STATUSES),))
                return [r[0] for r in cur.fetchall()]
        except Exception:
            self._reset_connection()
            raise

    def get_account(self) -> Optional[AccountSummary]:
        data = self.bus.get_json(settings.key_account_snapshot)
        return AccountSummary(**data) if data else None

    def get_symbol_spec(self, pair: str) -> Optional[SymbolSpec]:
        data = self.bus.get_json(settings.key_symbol_spec_prefix + pair)
        return SymbolSpec(**data) if data else None

    def assess(self, signal: Signal) -> RiskAssessment:
        try:
            active_pairs = self.get_active_pairs()
            account = self.get_account()
            spec = self.get_symbol_spec(signal.pair)
        except Exception as e:
            return _reject(f"Error leyendo estado (BD/Redis): {e}")
        return evaluate_signal(signal, account, spec, active_pairs)

    def reserve_order(self, signal: Signal, assessment: RiskAssessment, side: OrderSide) -> int:
        """
        Inserta la orden como 'pending' ANTES de publicarla. Así cuenta para los
        límites desde ya (sin carreras entre dos señales seguidas) y el
        execution-engine puede ejecutarla de forma idempotente.
        """
        try:
            with self._get_connection().cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders (pair, direction, units, volume_lots, entry_price, stop_loss, take_profit,
                                        status, strategy_name, signal_id, risk_amount, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        signal.pair,
                        side.value,
                        assessment.recommended_units,
                        assessment.recommended_lots,
                        signal.entry_price,
                        assessment.stop_loss,
                        assessment.take_profit,
                        signal.strategy_name,
                        signal.metadata.get("db_signal_id"),
                        assessment.risk_amount_usd,
                        Json({"signal": signal.model_dump(mode="json")}),
                    ),
                )
                return cur.fetchone()[0]
        except Exception:
            self._reset_connection()
            raise

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
