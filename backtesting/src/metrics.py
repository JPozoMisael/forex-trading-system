"""
Cálculo de métricas cuantitativas y estadísticas financieras para evaluación de estrategias.
"""
from dataclasses import dataclass
from typing import List, Dict, Any
import numpy as np
import pandas as pd


@dataclass
class BacktestMetrics:
    initial_balance: float
    final_balance: float
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    max_drawdown_usd: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    payoff_ratio: float
    expectancy_usd: float
    avg_trade_pnl: float
    max_consecutive_losses: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Capital Inicial ($)": f"${self.initial_balance:,.2f}",
            "Capital Final ($)": f"${self.final_balance:,.2f}",
            "Retorno Total (%)": f"{self.total_return_pct:+.2f}%",
            "CAGR (%)": f"{self.cagr_pct:+.2f}%",
            "Ratio Sharpe (Anualizado)": f"{self.sharpe_ratio:.2f}",
            "Ratio Sortino": f"{self.sortino_ratio:.2f}",
            "Drawdown Máximo (%)": f"-{self.max_drawdown_pct:.2f}%",
            "Drawdown Máximo ($)": f"-${self.max_drawdown_usd:,.2f}",
            "Total Operaciones": self.total_trades,
            "Operaciones Ganadoras": self.winning_trades,
            "Operaciones Perdedoras": self.losing_trades,
            "Tasa de Acierto (Win Rate)": f"{self.win_rate_pct:.1f}%",
            "Factor de Beneficio (Profit Factor)": f"{self.profit_factor:.2f}",
            "Ratio Payoff (Avg Win / Avg Loss)": f"{self.payoff_ratio:.2f}",
            "Expectativa por Trade ($)": f"${self.expectancy_usd:+.2f}",
            "PnL Promedio por Trade ($)": f"${self.avg_trade_pnl:+.2f}",
            "Máx Pérdidas Consecutivas": self.max_consecutive_losses,
        }


def calculate_metrics(
    trades: List[Dict[str, Any]],
    equity_series: pd.Series,
    initial_balance: float = 10000.0,
    risk_free_rate: float = 0.02,
) -> BacktestMetrics:
    """
    Calcula todas las métricas de rendimiento a partir del historial de trades y la curva de capital.
    """
    if not trades or len(trades) == 0:
        return BacktestMetrics(
            initial_balance=initial_balance,
            final_balance=initial_balance,
            total_return_pct=0.0,
            cagr_pct=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_usd=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            payoff_ratio=0.0,
            expectancy_usd=0.0,
            avg_trade_pnl=0.0,
            max_consecutive_losses=0,
        )

    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    final_balance = equity_series.iloc[-1] if not equity_series.empty else initial_balance
    total_return_pct = ((final_balance - initial_balance) / initial_balance) * 100.0

    total_trades = len(trades)
    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate = (winning_trades / total_trades) * 100.0 if total_trades > 0 else 0.0

    gross_profit = wins.sum() if len(wins) > 0 else 0.0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.0

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    prob_win = win_rate / 100.0
    prob_loss = 1.0 - prob_win
    expectancy_usd = (prob_win * avg_win) - (prob_loss * avg_loss)
    avg_trade_pnl = float(pnls.mean())

    # Drawdown
    if not equity_series.empty:
        cum_max = equity_series.cummax()
        drawdown_usd = cum_max - equity_series
        drawdown_pct = (drawdown_usd / cum_max) * 100.0
        max_dd_pct = float(drawdown_pct.max())
        max_dd_usd = float(drawdown_usd.max())
    else:
        max_dd_pct = 0.0
        max_dd_usd = 0.0

    # Ratios Sharpe y Sortino
    if not equity_series.empty and len(equity_series) > 1:
        returns = equity_series.pct_change().dropna()
        n_periods_per_year = 252 * 288  # aprox velas de 5 min al año
        mean_ret = returns.mean() * n_periods_per_year
        std_ret = returns.std() * np.sqrt(n_periods_per_year)

        sharpe = (mean_ret - risk_free_rate) / std_ret if std_ret > 0 else 0.0

        downside_returns = returns[returns < 0]
        downside_std = downside_returns.std() * np.sqrt(n_periods_per_year) if len(downside_returns) > 0 else 0.0
        sortino = (mean_ret - risk_free_rate) / downside_std if downside_std > 0 else 0.0

        # Estimación CAGR (días transcurridos)
        days = max(1.0, (equity_series.index[-1] - equity_series.index[0]).total_seconds() / 86400.0) if hasattr(equity_series.index, "total_seconds") else len(equity_series) / 288
        cagr = (((final_balance / initial_balance) ** (365.0 / days)) - 1.0) * 100.0 if days > 30 and final_balance > 0 else total_return_pct
    else:
        sharpe = 0.0
        sortino = 0.0
        cagr = total_return_pct

    # Máximas pérdidas consecutivas
    consecutive_losses = 0
    max_consec_losses = 0
    for p in pnls:
        if p < 0:
            consecutive_losses += 1
            max_consec_losses = max(max_consec_losses, consecutive_losses)
        else:
            consecutive_losses = 0

    return BacktestMetrics(
        initial_balance=round(initial_balance, 2),
        final_balance=round(final_balance, 2),
        total_return_pct=round(total_return_pct, 2),
        cagr_pct=round(cagr, 2),
        sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        max_drawdown_usd=round(max_dd_usd, 2),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(profit_factor, 2),
        payoff_ratio=round(payoff_ratio, 2),
        expectancy_usd=round(expectancy_usd, 2),
        avg_trade_pnl=round(avg_trade_pnl, 2),
        max_consecutive_losses=max_consec_losses,
    )
