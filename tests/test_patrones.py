"""
Estudio de patrones: encuentra un patrón real que se repite en las dos épocas
y el control de azar no lo confunde con casualidad.
"""
import numpy as np
import pandas as pd
import pytest

from ml.tradingview import TV_COLUMNS
from run_patrones import bin_column, chance_count, pattern_rows, repeated, split_epochs


@pytest.fixture(scope="module")
def planted():
    """Horas al azar, pero en la sesión de Londres la compra gana el 60 % (lo normal, 35 %)."""
    rng = np.random.default_rng(1)
    n = 20000
    idx = pd.date_range("2012-01-01", periods=n, freq="h", tz="UTC")
    data = pd.DataFrame({c: rng.normal(size=n) for c in TV_COLUMNS}, index=idx)
    for c in ("tend_h1", "tend_h4", "tend_d", "fib_mov", "vela_envolvente", "vela_martillo"):
        data[c] = rng.choice([-1.0, 0.0, 1.0], n)
    data["alineacion"] = data["tend_h1"] + data["tend_h4"] + data["tend_d"]
    data["sesion"] = rng.choice([0.0, 1.0, 2.0, 3.0], n)
    p_long = np.where(data["sesion"] == 1, 0.60, 0.35)
    data["res_long"] = np.where(rng.random(n) < p_long, "tp", "sl")
    data["res_short"] = np.where(rng.random(n) < 0.35, "tp", "sl")
    return data


def test_finds_the_planted_pattern_in_both_epochs(planted):
    a, b = split_epochs(planted)
    rep = repeated(pattern_rows(a, b))
    top = rep[0]
    assert (top["herramienta"], top["situación"], top["lado"]) == ("sesion", "Londres", "Compra")
    assert top["ventaja_1"] > 0.15 and top["ventaja_2"] > 0.15
    # Por azar casi nada se repite; el patrón sembrado no aparece al desordenar el futuro
    assert chance_count(a, b, n=5) < len(rep)


def test_quintile_cuts_come_from_the_first_epoch():
    a = pd.Series(np.arange(101.0))  # quintiles exactos: 20, 40, 60, 80
    b = pd.Series([-5.0, 50.0, 500.0])
    assert bin_column("rsi", a, b).tolist() == ["menos de 20", "40 a 60", "80 o más"]
