"""
Validación fuera de muestra de cualquier estrategia (BaseStrategy).

Walk-forward con ventana creciente: para cada ventana la estrategia se
construye SOLO con el pasado (`factory(train_df, ventana)`; una estrategia de
reglas lo ignora, el modelo de ML entrena con él) y se prueba en los meses
siguientes, que nunca vio. El saldo se encadena de una ventana a la siguiente,
así la curva fuera de muestra es una sola, como si se hubiera operado en vivo.

Del script de Colab (research/colab/sistema_trading.py) se portan:
  - Test de permutación: cada operación se compara con su operación OPUESTA
    (mismo momento, misma distancia de SL/TP, dirección contraria, con
    costos). Si eligiendo la dirección al azar se iguala el resultado real
    con frecuencia, no hay evidencia de ventaja.
  - Intervalo de confianza por bootstrap de la expectativa (en R).
  - Veredicto: VENTAJA CON EVIDENCIA / POSITIVO PERO NO CONCLUYENTE /
    SIN VENTAJA / INSUFICIENTE.

Líneas base en cada ventana, con los mismos costos: no operar y buy & hold.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from engine import BacktestEngine
from metrics import BacktestMetrics, calculate_metrics
from shared.fx import STANDARD_LOT, get_pip_size, quote_to_usd_rate
from shared.models import OrderSide, Signal
from strategies.base_strategy import BaseStrategy

NO_TRADE = "Sin operar"
BUY_AND_HOLD = "Buy & Hold"


@dataclass
class Window:
    n: int
    train_start: pd.Timestamp
    test_start: pd.Timestamp   # = fin (exclusivo) del entrenamiento
    test_end: pd.Timestamp     # exclusivo


@dataclass
class StrategyOOS:
    """Resultado fuera de muestra de una estrategia en todas las ventanas."""
    name: str
    equity: pd.Series
    trades: pd.DataFrame
    metrics: BacktestMetrics
    per_window: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[int, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class Verdict:
    label: str
    explanation: str
    n_trades: int
    expectancy_r: float
    p_value: float
    ci_low: float
    ci_high: float


@dataclass
class ValidationReport:
    pair: str
    granularity: str
    windows: List[Window]
    results: Dict[str, StrategyOOS]
    verdicts: Dict[str, Verdict]
    costs: Dict[str, float]


StrategyFactory = Callable[[pd.DataFrame, Window], BaseStrategy]


class ActiveFrom(BaseStrategy):
    """Envuelve una estrategia para que solo dé señales desde `start` (el inicio de la prueba)."""

    def __init__(self, inner: BaseStrategy, start: pd.Timestamp):
        super().__init__(name=inner.name)
        self.inner = inner
        self.start = start

    @property
    def min_candles_required(self) -> int:
        return self.inner.min_candles_required

    @property
    def lookback_window(self) -> int:
        return self.inner.lookback_window

    def evaluate(self, df: pd.DataFrame, pair: str) -> Optional[Signal]:
        if df.index[-1] < self.start:
            return None
        return self.inner.evaluate(df, pair)


def make_windows(index: pd.DatetimeIndex, initial_train_years: float, test_months: int,
                 first_test_start: Optional[pd.Timestamp] = None) -> List[Window]:
    """
    Ventanas consecutivas de prueba; el entrenamiento siempre empieza en el
    primer dato. La primera prueba empieza tras `initial_train_years` o en
    `first_test_start` si se indica. Un sobrante final de menos de un mes se une
    a la última ventana.
    """
    first, end = index[0], index[-1] + pd.Timedelta(seconds=1)
    # En meses: DateOffset no acepta años fraccionarios (p.ej. 2.5 en config.yaml)
    test_start = first + pd.DateOffset(months=max(1, int(round(initial_train_years * 12))))
    if first_test_start is not None:
        test_start = max(test_start, first_test_start)
    windows = []
    while test_start < end:
        test_end = min(test_start + pd.DateOffset(months=test_months), end)
        if end - test_end < pd.Timedelta(days=30):
            test_end = end
        windows.append(Window(n=len(windows) + 1, train_start=first, test_start=test_start, test_end=test_end))
        test_start = test_end
    return windows


def opposite_r(trade: Dict[str, Any], seg: pd.DataFrame, pair: str, spread_pips: float,
               slippage_pips: float, commission_per_lot: float) -> float:
    """
    Resultado en R de la operación contraria: misma vela de entrada, mismas
    distancias de SL/TP, dirección opuesta y los mismos costos.
    """
    pip = get_pip_size(pair)
    hs, slip = spread_pips * pip / 2, slippage_pips * pip
    i0 = seg.index.get_loc(trade["entry_time"])
    o = float(seg["open"].iloc[i0])
    was_buy = trade["side"] == OrderSide.BUY.value
    side = OrderSide.SELL if was_buy else OrderSide.BUY
    entry = o + hs + slip if side == OrderSide.BUY else o - hs - slip
    sl_d = abs(trade["entry_price"] - trade["stop_loss"])
    tp_d = abs(trade["take_profit"] - trade["entry_price"])
    opp = {
        "side": side,
        "stop_loss": entry - sl_d if side == OrderSide.BUY else entry + sl_d,
        "take_profit": entry + tp_d if side == OrderSide.BUY else entry - tp_d,
    }

    exit_price = None
    for i in range(i0, len(seg)):
        exit_price, reason = BacktestEngine._check_exit(
            opp, float(seg["open"].iloc[i]), float(seg["high"].iloc[i]), float(seg["low"].iloc[i]), hs, slip
        )
        if reason is not None:
            break
    if exit_price is None:
        c = float(seg["close"].iloc[-1])
        exit_price = c - hs - slip if side == OrderSide.BUY else c + hs + slip

    diff = (exit_price - entry) if side == OrderSide.BUY else (entry - exit_price)
    units = trade["units"]
    pnl = diff * units * quote_to_usd_rate(pair, exit_price) - units / STANDARD_LOT * commission_per_lot
    risk = sl_d * units * quote_to_usd_rate(pair, entry)
    return pnl / risk if risk > 0 else 0.0


def trade_r(trade: Dict[str, Any], pair: str) -> float:
    risk = abs(trade["entry_price"] - trade["stop_loss"]) * trade["units"] * quote_to_usd_rate(pair, trade["entry_price"])
    return trade["pnl"] / risk if risk > 0 else 0.0


def buy_and_hold(seg: pd.DataFrame, balance: float, pair: str, spread_pips: float,
                 slippage_pips: float, commission_per_lot: float):
    """Compra al inicio de la ventana con el saldo como nocional (1x) y vende al final."""
    pip = get_pip_size(pair)
    hs, slip = spread_pips * pip / 2, slippage_pips * pip
    entry = float(seg["open"].iloc[0]) + hs + slip
    units = int(balance / (entry * quote_to_usd_rate(pair, entry)))
    comm = units / STANDARD_LOT * commission_per_lot
    bid = seg["close"] - hs
    rate = bid.map(lambda p: quote_to_usd_rate(pair, p))
    equity = balance + (bid - entry) * units * rate - comm
    exit_price = float(seg["close"].iloc[-1]) - hs - slip
    pnl = (exit_price - entry) * units * quote_to_usd_rate(pair, exit_price) - comm
    equity.iloc[-1] = balance + pnl
    trade = {"pnl": round(pnl, 2), "side": "buy", "entry_time": seg.index[0], "exit_time": seg.index[-1]}
    return equity.rename("Equity"), [trade]


def permutation_p_value(r: np.ndarray, r_opp: np.ndarray, n_perm: int, seed: int = 7) -> float:
    """P(azar >= real): cada operación toma su R o el de la opuesta con probabilidad 1/2."""
    if len(r) == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    sims = np.where(rng.random((n_perm, len(r))) < 0.5, r, r_opp).sum(axis=1)
    return float((np.sum(sims >= r.sum()) + 1) / (n_perm + 1))


def bootstrap_ci(r: np.ndarray, n_boot: int = 2000, seed: int = 11):
    """Intervalo de confianza al 95% de la expectativa por operación (en R)."""
    if len(r) < 5:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n_boot, len(r))).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def make_verdict(trades: pd.DataFrame, n_perm: int, min_trades: int, min_expectancy_r: float) -> Verdict:
    n = len(trades)
    if n == 0:
        return Verdict("SIN OPERACIONES", "La estrategia no operó fuera de muestra.", 0, float("nan"),
                       float("nan"), float("nan"), float("nan"))
    r, r_opp = trades["r"].to_numpy(), trades["r_opposite"].to_numpy()
    exp = float(r.mean())
    p = permutation_p_value(r, r_opp, n_perm)
    lo, hi = bootstrap_ci(r)
    if n < min_trades:
        label, text = "INSUFICIENTE", f"Solo {n} operaciones fuera de muestra: no alcanza para concluir. Solo demo."
    elif exp > min_expectancy_r and p < 0.05 and lo > 0:
        label, text = ("VENTAJA CON EVIDENCIA", "Gana fuera de muestra, el intervalo de confianza no incluye el cero y "
                       "el azar casi nunca la iguala. Apta para demo prolongada y luego real con riesgo bajo.")
    elif exp > 0:
        label, text = ("POSITIVO PERO NO CONCLUYENTE", "Gana fuera de muestra pero podría ser suerte "
                       "(p >= 0.05 o el intervalo incluye el cero). Solo demo hasta acumular evidencia.")
    else:
        label, text = "SIN VENTAJA", "Pierde fuera de muestra después de costos. Solo demo."
    return Verdict(label, text, n, exp, p, lo, hi)


def _window_row(window: Window, metrics: BacktestMetrics, start_balance: float) -> Dict[str, Any]:
    return {
        "ventana": window.n,
        "desde": window.test_start,
        "hasta": window.test_end,
        "capital_inicial": round(start_balance, 2),
        "retorno_pct": metrics.total_return_pct,
        "max_dd_pct": metrics.max_drawdown_pct,
        "sharpe": metrics.sharpe_ratio,
        "profit_factor": metrics.profit_factor,
        "win_rate_pct": metrics.win_rate_pct,
        "operaciones": metrics.total_trades,
    }


def validate(
    df: pd.DataFrame,
    factories: Dict[str, StrategyFactory],
    pair: str,
    granularity: str,
    cfg: Dict[str, Any],
    log: Callable[[str], None] = print,
    periodo: Optional[str] = None,
) -> ValidationReport:
    """
    Corre el walk-forward para cada estrategia de `factories` más las líneas
    base (no operar, buy & hold). `cfg` es el contenido de config.yaml.

    periodo (con `validacion.inicio_prueba_final` en config.yaml):
      "desarrollo": solo velas ANTERIORES al corte. Es donde se comparan
                    variantes; la prueba final ni se carga.
      "final":      ventanas de prueba desde el corte (el entrenamiento usa
                    todo lo anterior). Se corre una vez con la variante elegida.
      None:         todas las velas.
    """
    if periodo is not None:
        cutoff = pd.Timestamp(cfg["validacion"]["inicio_prueba_final"], tz="UTC")
        if periodo == "desarrollo":
            df = df[df.index < cutoff]
        elif periodo != "final":
            raise ValueError(f"periodo debe ser 'desarrollo', 'final' o None (no '{periodo}')")
    costs = {
        "spread_pips": float(cfg["costes"]["spread_pips"]),
        "slippage_pips": float(cfg["costes"]["slippage_pips"]),
        "commission_per_lot": float(cfg["costes"]["comision_por_lote"]),
    }
    engine_kwargs = dict(
        risk_per_trade_pct=float(cfg["riesgo"]["riesgo_por_operacion_pct"]),
        max_daily_loss_pct=float(cfg["riesgo"]["perdida_diaria_max_pct"]),
        **costs,
    )
    capital = float(cfg["riesgo"]["capital_inicial"])
    val = cfg["validacion"]
    windows = make_windows(df.index, val["entrenamiento_inicial_anios"], int(val["ventana_prueba_meses"]),
                           first_test_start=cutoff if periodo == "final" else None)
    if not windows:
        raise ValueError("No hay datos suficientes para ninguna ventana de prueba")

    names = [NO_TRADE, BUY_AND_HOLD] + list(factories)
    balance = {name: capital for name in names}
    equities: Dict[str, List[pd.Series]] = {name: [] for name in names}
    trades: Dict[str, List[Dict[str, Any]]] = {name: [] for name in names}
    rows: Dict[str, List[Dict[str, Any]]] = {name: [] for name in names}
    diagnostics: Dict[str, Dict[int, Dict[str, Any]]] = {name: {} for name in names}

    for w in windows:
        test = df[(df.index >= w.test_start) & (df.index < w.test_end)]
        if len(test) < 2:
            continue
        train = df[df.index < w.test_start]
        log(f"[ventana {w.n}/{len(windows)}] entrena {train.index[0]:%Y-%m-%d} a {w.test_start:%Y-%m-%d} | "
            f"prueba {w.test_start:%Y-%m-%d} a {w.test_end:%Y-%m-%d}")

        # Líneas base
        eq = pd.Series(balance[NO_TRADE], index=test.index, name="Equity")
        equities[NO_TRADE].append(eq)
        rows[NO_TRADE].append(_window_row(w, calculate_metrics([], eq, balance[NO_TRADE]), balance[NO_TRADE]))

        eq, bh_trades = buy_and_hold(test, balance[BUY_AND_HOLD], pair, **costs)
        rows[BUY_AND_HOLD].append(_window_row(w, calculate_metrics(bh_trades, eq, balance[BUY_AND_HOLD]), balance[BUY_AND_HOLD]))
        equities[BUY_AND_HOLD].append(eq)
        trades[BUY_AND_HOLD].extend(bh_trades)
        balance[BUY_AND_HOLD] = float(eq.iloc[-1])

        # Estrategias
        for name, factory in factories.items():
            strategy = factory(train, w)
            warmup = max(strategy.lookback_window, strategy.min_candles_required)
            i0 = df.index.searchsorted(w.test_start)
            seg = df.iloc[max(0, i0 - warmup): i0 + len(test)]
            engine = BacktestEngine(initial_balance=balance[name], **engine_kwargs)
            res = engine.run(ActiveFrom(strategy, w.test_start), seg, pair=pair)
            eq = res.equity_series[res.equity_series.index >= w.test_start]
            for t in res.trades:
                t["ventana"] = w.n
                t["r"] = trade_r(t, pair)
                t["r_opposite"] = opposite_r(t, seg, pair, **costs)
            rows[name].append(_window_row(w, calculate_metrics(res.trades, eq, balance[name]), balance[name]))
            equities[name].append(eq)
            trades[name].extend(res.trades)
            balance[name] = float(eq.iloc[-1]) if len(eq) else balance[name]
            if hasattr(strategy, "diagnostics"):
                diagnostics[name][w.n] = strategy.diagnostics(df[df.index < w.test_end], w.test_start)

    results, verdicts = {}, {}
    for name in names:
        equity = pd.concat(equities[name]) if equities[name] else pd.Series(dtype=float)
        tdf = pd.DataFrame(trades[name])
        results[name] = StrategyOOS(
            name=name,
            equity=equity,
            trades=tdf,
            metrics=calculate_metrics(trades[name], equity, initial_balance=capital),
            per_window=rows[name],
            diagnostics=diagnostics[name],
        )
        if name in factories:
            verdicts[name] = make_verdict(tdf, int(val["n_permutaciones"]), int(val["min_operaciones"]),
                                          float(val["expectativa_minima_r"]))

    return ValidationReport(pair=pair, granularity=granularity, windows=windows, results=results,
                            verdicts=verdicts, costs=costs)
