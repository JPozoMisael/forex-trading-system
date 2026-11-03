"""
El strategy-engine guarda lo que vio el modelo en cada vela; si la BD falla,
la evaluación sigue (es solo registro para el reporte).
"""
from datetime import datetime, timezone

from signal_publisher import SignalPublisher


class _Cursor:
    def __init__(self, log, fail):
        self.log, self.fail = log, fail

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        if self.fail:
            raise RuntimeError("BD caída")
        self.log.append((sql, params))


class _Conn:
    closed = False

    def __init__(self, fail=False):
        self.log, self.fail = [], fail

    def cursor(self):
        return _Cursor(self.log, self.fail)


def _publisher(conn):
    pub = SignalPublisher.__new__(SignalPublisher)
    pub._conn, pub.dsn = conn, "x"
    return pub


STATE = {"p_long": 0.42, "p_short": 0.3, "umbral": 0.5, "cierre": 1.1, "atr": 0.001, "sl_atr": 2.0, "tp_atr": 3.0,
         "horizonte_velas": 48, "senal": None, "factores": {"long": [], "short": []}}
TS = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def test_saves_every_evaluation_with_upsert():
    conn = _Conn()
    assert _publisher(conn).save_evaluation("EUR_USD", "H1", TS, "ML_LightGBM", STATE)
    sql, params = conn.log[0]
    assert "INSERT INTO model_evaluations" in sql and "ON CONFLICT" in sql
    assert params[:7] == ("EUR_USD", "H1", TS, "ML_LightGBM", 0.42, 0.3, 0.5) and params[7] == 1.1


def test_db_failure_does_not_stop_the_evaluation():
    pub = _publisher(_Conn(fail=True))
    assert pub.save_evaluation("EUR_USD", "H1", TS, "ML_LightGBM", STATE) is False
    assert pub._conn is None  # se reconecta la próxima vez


def test_rule_strategies_without_probabilities_are_not_saved():
    conn = _Conn()
    assert _publisher(conn).save_evaluation("EUR_USD", "H1", TS, "MA_Crossover", {"senal": None}) is False
    assert conn.log == []
