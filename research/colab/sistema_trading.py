# =====================================================================================
#  SISTEMA DE TRADING TODO EN UNO — Google Colab
#  Una sola ejecución hace todo:
#   1) Descarga ~2 años de velas de 15m (Dukascopy, gratis; caché incremental)
#   2) Valida la estrategia por activo y por modo de entrada, sin mirar el futuro y con costos:
#        · "confirmacion": entrada a mercado tras vela de 30m que reacciona en zona/liquidez/EMA
#        · "limite": orden limitada en la zona más cercana, colocada a la apertura de Londres
#      Aprueba activo+modo SOLO con datos in-sample y juzga el conjunto fuera de muestra
#      (test de permutación + intervalo de confianza).
#   3) Entrega las ÓRDENES DE HOY solo para lo aprobado: entrada, SL, TP, lotes, validez,
#      con la etiqueta APTA PARA REAL o SOLO DEMO según el veredicto, gráficos y un PDF.
#  Re-ejecutar cada vez que quieras órdenes actualizadas (la descarga después es de segundos).
#
#  DATOS EN TIEMPO REAL CON TU CUENTA XM (MetaTrader 5):
#   · Solo en Windows, con MetaTrader 5 abierto y con sesión iniciada en tu cuenta XM.
#   · En tu PC: pip install MetaTrader5 pandas numpy matplotlib reportlab dukascopy-python
#     y ejecutar:  python sistema_trading.py   (o en Jupyter / VS Code)
#   · Con FUENTE = "auto" usa MT5 en Windows y Dukascopy en Colab. Con MT5 los precios, el spread,
#     el tamaño de contrato, el saldo y los lotes salen directamente de tu cuenta.
#  No es asesoría financiera: resultados pasados no garantizan resultados futuros.
# =====================================================================================
import subprocess, sys
for _pkg, _mod in (("yfinance", "yfinance"), ("dukascopy-python", "dukascopy_python"), ("reportlab", "reportlab")):
    try:
        __import__(_mod)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", _pkg], check=False)

import os, glob, time, warnings, html, datetime as dt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
try:
    from IPython import get_ipython
    EN_NOTEBOOK = get_ipython() is not None
except Exception:
    EN_NOTEBOOK = False
if EN_NOTEBOOK:
    from IPython.display import display, Markdown, Image
else:                                                    # ejecución en consola (python sistema_trading.py)
    class Markdown(str): pass
    class Image:
        def __init__(self, ruta): self.ruta = ruta
    def display(x):
        if isinstance(x, Image): print(f"[gráfico guardado: {x.ruta}]")
        elif hasattr(x, "data") and isinstance(getattr(x, "data"), pd.DataFrame): print(x.data.to_string())
        elif hasattr(x, "to_string"): print(x.to_string())
        else: print(x)
warnings.filterwarnings("ignore")

# ------------------------------------ CONFIGURACIÓN ------------------------------------
FUENTE        = "auto"                # "auto" | "mt5" (tiempo real, XM) | "dukascopy" | "mt5_csv" | "yahoo_1h"
ANIOS         = 2                     # historia a probar (desde hoy hacia atrás)
TZ            = "America/Guayaquil"
CARPETA       = "sistema"             # resultados
CARPETA_DATOS = "datos_bt"            # caché de datos descargados (se reutiliza al re-ejecutar)
USAR_DRIVE    = False                 # True = guardar la caché en tu Google Drive (no se pierde al cerrar Colab)
MT5_TZ        = "auto"                # zona horaria del servidor MT5 ("auto" la detecta; XM usa GMT+2/+3)
MT5_RUTA      = None                  # ruta a terminal64.exe si tienes varios MT5 instalados (si no, None)
MT5_LOGIN, MT5_CLAVE, MT5_SERVIDOR = None, None, None   # opcional: si MT5 no tiene la sesión iniciada

# Parámetros de la estrategia (los mismos del script en vivo)
RR_MIN, RR_MAX, RR_OBJ = 1.5, 3.0, 2.0
UMBRAL_SESGO  = 3
MAX_USD_LADO  = 2
RIESGO_PCT    = 1.0
CAPITAL       = 1000                  # capital para simular la curva del backtest

# Costos por operación (ida y vuelta) en pips del activo, aprox. de una cuenta estándar
SPREAD = {"EURUSD": 1.0, "GBPUSD": 1.5, "USDJPY": 1.2, "AUDUSD": 1.3, "USDCAD": 1.8, "USDCHF": 1.8,
          "XAUUSD": 3.0, "BTCUSD": 30.0}
SLIPPAGE_PIPS = 0.3                   # por lado

SOLO_APROBADOS = False                # True = órdenes solo de lo que pasó la validación; False = todas (marcadas)
CUENTA_USD    = 1000                  # saldo para el tamaño de lote (con MT5 se toma el saldo real de tu cuenta)

# Validación
OOS_FRACCION  = 0.30                  # último 30% del periodo = fuera de muestra
MIN_IS        = 15                    # operaciones mínimas in-sample para aprobar un activo/modo
EXP_MIN_IS    = 0.05                  # expectativa mínima in-sample (R) para aprobar
N_PERMUT      = 2000
MIN_TRADES    = 30

ACTIVOS = ["XAUUSD", "EURUSD", "BTCUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"]
PIP = {"XAUUSD": 0.1, "BTCUSD": 1.0, "USDJPY": 0.01}
USD_AL_COMPRAR = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "XAUUSD": -1,
                  "USDJPY": 1, "USDCAD": 1, "USDCHF": 1, "BTCUSD": 0}
# ----------------------------------------------------------------------------------------

if USAR_DRIVE:
    from google.colab import drive
    drive.mount("/content/drive"); CARPETA_DATOS = "/content/drive/MyDrive/trading_datos"
os.makedirs(CARPETA, exist_ok=True); os.makedirs(CARPETA_DATOS, exist_ok=True)
NY, LON, TOK = "America/New_York", "Europe/London", "Asia/Tokyo"
DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
pip_de = lambda n: PIP.get(n, 0.0001)
fmt_hora = lambda t: f"{DIAS_ES[t.weekday()]} {t:%d-%m-%Y %H:%M}"
HOY = pd.Timestamp.now(tz=TZ)
DESDE = (HOY - pd.DateOffset(years=ANIOS)).normalize()


# ======================================== DATOS ========================================
def _ohlc(df):
    df = df.rename(columns=str.title)[["Open", "High", "Low", "Close"]].astype(float).dropna()
    return df[~df.index.duplicated()].sort_index()

def cargar_dukascopy(n):
    import dukascopy_python as dk
    from dukascopy_python import instruments as I
    inst = {"EURUSD": I.INSTRUMENT_FX_MAJORS_EUR_USD, "GBPUSD": I.INSTRUMENT_FX_MAJORS_GBP_USD,
            "USDJPY": I.INSTRUMENT_FX_MAJORS_USD_JPY, "AUDUSD": I.INSTRUMENT_FX_MAJORS_AUD_USD,
            "USDCAD": I.INSTRUMENT_FX_MAJORS_USD_CAD, "USDCHF": I.INSTRUMENT_FX_MAJORS_USD_CHF,
            "XAUUSD": I.INSTRUMENT_FX_METALS_XAU_USD, "BTCUSD": I.INSTRUMENT_VCCY_BTC_USD}[n]
    cache = f"{CARPETA_DATOS}/{n}_15m.csv"
    previo = pd.read_csv(cache, index_col=0, parse_dates=True) if os.path.exists(cache) else pd.DataFrame()
    ini = DESDE.tz_convert("UTC").tz_localize(None).to_pydatetime()
    if len(previo):
        previo.index = pd.to_datetime(previo.index, utc=True)
        if previo.index[0] <= pd.Timestamp(ini, tz="UTC") + pd.Timedelta(days=7):
            ini = (previo.index[-1] - pd.Timedelta(hours=1)).tz_localize(None).to_pydatetime()
        else:
            previo = pd.DataFrame()
    fin = HOY.tz_convert("UTC").tz_localize(None).to_pydatetime()
    partes, a = [previo] if len(previo) else [], ini
    while a < fin:
        b = min(a + dt.timedelta(days=90), fin)
        for intento in range(4):
            try:
                p = dk.fetch(inst, dk.INTERVAL_MIN_15, dk.OFFER_SIDE_BID, a, b)
                break
            except Exception as e:
                if intento == 3: raise
                time.sleep(5)
        if len(p):
            p = p[~((p.high == p.low) & (p.get("volume", 1) == 0))]
            partes.append(_ohlc(p))
        print(f"   {n}: {a:%Y-%m-%d} → {b:%Y-%m-%d} ({len(p)} velas)")
        a = b
    df = pd.concat(partes); df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_csv(cache)
    return df

def cargar_mt5(n):
    alias = {"XAUUSD": ["XAUUSD", "GOLD"], "BTCUSD": ["BTCUSD", "BTC"]}.get(n, [n])
    arch = [f for f in glob.glob("mt5/*") if any(a in os.path.basename(f).upper() for a in alias)]
    if not arch:
        return pd.DataFrame()
    raw = pd.read_csv(arch[0], sep=None, engine="python")
    raw.columns = [c.strip("<>").upper() for c in raw.columns]
    t = pd.to_datetime(raw["DATE"].astype(str) + " " + raw.get("TIME", "00:00:00").astype(str), format="mixed")
    df = raw.rename(columns={"OPEN": "Open", "HIGH": "High", "LOW": "Low", "CLOSE": "Close"})
    df.index = t.dt.tz_localize(MT5_TZ, ambiguous="NaT", nonexistent="shift_forward")
    df = _ohlc(df[df.index.notna()])
    paso = pd.Series(df.index).diff().median()
    if paso < pd.Timedelta(minutes=15):
        df = df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return df

def cargar_yahoo(n):
    import yfinance as yf
    tk = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD"}.get(n, f"{n}=X")
    df = yf.download(tk, period="730d", interval="1h", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return _ohlc(df) if len(df) else df

# ------------------------------- MetaTrader 5 en vivo (XM) -------------------------------
MT5, ACC, SIMB, MT5_INFO, SRV_TZ = None, None, {}, {}, None

def iniciar_mt5():
    global MT5, ACC
    try:
        import MetaTrader5 as mt5
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "MetaTrader5"], check=False)
        import MetaTrader5 as mt5
    kw = {"path": MT5_RUTA} if MT5_RUTA else {}
    if MT5_LOGIN:
        kw.update(login=int(MT5_LOGIN), password=MT5_CLAVE, server=MT5_SERVIDOR)
    if not mt5.initialize(**kw):
        raise RuntimeError(f"no se pudo conectar ({mt5.last_error()}). Abre MetaTrader 5 e inicia sesión en tu cuenta XM")
    MT5, ACC = mt5, mt5.account_info()
    print(f"🔌 MT5 conectado: cuenta {ACC.login} · {ACC.server} · saldo {ACC.balance:,.2f} {ACC.currency}"
          f"{' · DEMO' if 'demo' in ACC.server.lower() else ''}")

def simbolo_mt5(n):
    alias = {"XAUUSD": ["GOLD", "XAUUSD"], "BTCUSD": ["BTCUSD", "BITCOIN"]}.get(n, [n])
    nombres = [x.name for x in MT5.symbols_get()]
    for a in alias:
        exacto = [x for x in nombres if x.upper() == a]
        if exacto: return exacto[0]
        pref = sorted([x for x in nombres if x.upper().startswith(a)], key=len)
        if pref: return pref[0]
    return None

def zona_servidor():
    if MT5_TZ != "auto": return MT5_TZ
    for n in ("EURUSD", "GBPUSD", "XAUUSD"):
        sym = simbolo_mt5(n); tick = MT5.symbol_info_tick(sym) if sym else None
        if tick:
            dif = (tick.time - time.time()) / 3600
            if 1.5 <= dif <= 3.5:                                   # mercado abierto: el tick es reciente
                off = round(dif)
                hel = pd.Timestamp.now(tz="Europe/Helsinki").utcoffset().total_seconds() / 3600
                return "Europe/Helsinki" if off == hel else f"Etc/GMT-{off}"
    return "Europe/Helsinki"                                         # mercado cerrado: horario habitual GMT+2/+3

def cargar_mt5_vivo(n):
    sym = simbolo_mt5(n)
    if not sym:
        print(f"   {n}: no está en tu lista de símbolos de MT5"); return pd.DataFrame()
    MT5.symbol_select(sym, True)
    r = MT5.copy_rates_range(sym, MT5.TIMEFRAME_M15, DESDE.tz_convert("UTC").to_pydatetime(),
                             (HOY + pd.Timedelta(days=1)).tz_convert("UTC").to_pydatetime())
    if r is None or len(r) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(r)
    df.index = pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s")).tz_localize(SRV_TZ, ambiguous="NaT", nonexistent="shift_forward")
    df = df[df.index.notna()]
    SIMB[n], MT5_INFO[n] = sym, MT5.symbol_info(sym)
    if df.index[0] > (DESDE + pd.Timedelta(days=45)).tz_convert(df.index.tz):
        print(f"   ⚠️ {n}: MT5 solo entrega historia desde {df.index[0]:%Y-%m-%d}. Para más, sube 'Máx. barras en el gráfico' "
              f"(Herramientas > Opciones > Gráficos) o valida con FUENTE = 'dukascopy'.")
    return _ohlc(df)

if FUENTE == "auto":
    FUENTE = "mt5" if sys.platform.startswith("win") else "dukascopy"
if FUENTE == "mt5":
    try:
        iniciar_mt5(); SRV_TZ = zona_servidor(); print(f"   horario del servidor: {SRV_TZ}")
    except Exception as e:
        print(f"⚠️  MT5: {e}. Se usa Dukascopy (con algunos minutos de retraso)."); FUENTE = "dukascopy"

DATOS = {}
print(f"Fuente: {FUENTE} · desde {DESDE:%Y-%m-%d}")
for n in ACTIVOS:
    try:
        df = {"dukascopy": cargar_dukascopy, "mt5_csv": cargar_mt5, "yahoo_1h": cargar_yahoo, "mt5": cargar_mt5_vivo}[FUENTE](n)
    except Exception as e:
        print(f"⚠️  {n}: error al descargar ({e}); se omite"); continue
    if df is None or len(df) < 500:
        print(f"⚠️  {n}: sin datos suficientes; se omite"); continue
    df.index = (df.index.tz_localize("UTC") if df.index.tz is None else df.index).tz_convert(TZ)
    df = df[df.index >= DESDE]
    _p = pd.Series(df.index).diff().median()
    df = df[df.index + _p <= HOY]                          # descartar la vela que todavía se está formando
    df.index = df.index.as_unit("ns")                     # todas las comparaciones internas usan nanosegundos
    DATOS[n] = df
    if n in MT5_INFO:                                      # spread real de tu cuenta (si es mayor que el supuesto)
        sp = MT5_INFO[n].spread * MT5_INFO[n].point / pip_de(n)
        SPREAD[n] = max(SPREAD.get(n, 0), sp)
    print(f"✅ {n:7s} {len(df):7,d} velas · {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}")
if not DATOS:
    raise SystemExit("No hay datos. Revisa FUENTE o la carpeta mt5/.")
PASO = pd.Series(next(iter(DATOS.values())).index).diff().median()
PASO = pd.Timedelta(hours=1) if PASO >= pd.Timedelta(minutes=50) else pd.Timedelta(minutes=15)
TF_ENTRADA = "30m" if PASO < pd.Timedelta(minutes=30) else "1h"
print(f"Base {PASO} · entradas en {TF_ENTRADA}")


# ================================ HERRAMIENTAS (mismas que el script en vivo) ================================
M30, H1, H4 = pd.Timedelta(minutes=30), pd.Timedelta(hours=1), pd.Timedelta(hours=4)

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))
def atr(df, n=14):
    pc = df.Close.shift()
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()
def remuestrear(df, regla):
    return df.resample(regla).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
def dia_trading(idx): return (idx.tz_convert(NY) + pd.Timedelta(hours=7)).date
def hora_local(fecha, hora, tz): return pd.Timestamp(f"{fecha} {hora}", tz=tz).tz_convert(TZ)
def cierre_dia(fecha): return hora_local(fecha, "17:00", NY)
def inicio_dia(fecha): return cierre_dia(fecha) - pd.Timedelta(days=1)
def inicio_semana(ts):
    ny = ts.tz_convert(NY); dom = (ny - pd.Timedelta(days=(ny.weekday() + 1) % 7)).date()
    ini = pd.Timestamp(f"{dom} 17:00", tz=NY)
    if ini > ny: ini = pd.Timestamp(f"{dom - pd.Timedelta(days=7)} 17:00", tz=NY)
    return ini.tz_convert(TZ)
def ventana_operativa(fecha):
    return hora_local(fecha, "08:00", LON), hora_local(fecha, "11:00" if pd.Timestamp(fecha).weekday() == 4 else "12:00", NY)
def en_blackout(t):
    ny = t.tz_convert(NY); m = ny.hour * 60 + ny.minute
    return 505 <= m <= 525 or 565 <= m <= 590
def sesion_de(ts):
    for n, (tz, h0, h1) in {"Londres": (LON, 8, 17), "NY": (NY, 8, 17), "Asia": (TOK, 9, 18)}.items():
        if h0 <= ts.tz_convert(tz).hour < h1: return n
    return "otra"

def estructura(df, paso, n=3):
    O, H, L, C = (df[c].values for c in ("Open", "High", "Low", "Close"))
    idx = df.index; N = len(df); cierre = idx + paso; a = atr(df).values
    swings, obs, fvgs = [], [], []
    tr = np.zeros(N, dtype=int); trend = 0; ult_h = ult_l = None; prev_h = prev_l = None
    for i in range(N):
        j = i - n
        if j >= n:
            if H[j] == H[j-n:j+n+1].max():
                lab = "HH" if prev_h is None or H[j] > prev_h else "LH"; prev_h = H[j]
                swings.append((idx[j].value, H[j], 1, cierre[i].value)); ult_h = dict(p=H[j], j=j, roto=False)
            if L[j] == L[j-n:j+n+1].min():
                lab = "LL" if prev_l is None or L[j] < prev_l else "HL"; prev_l = L[j]
                swings.append((idx[j].value, L[j], -1, cierre[i].value)); ult_l = dict(p=L[j], j=j, roto=False)
        for d, ult, otro in ((1, ult_h, ult_l), (-1, ult_l, ult_h)):
            if ult and not ult["roto"] and (C[i] > ult["p"] if d == 1 else C[i] < ult["p"]):
                ult["roto"] = True; trend = d
                lim = max(otro["j"] if otro else 0, i - 25)
                for k in range(i - 1, lim - 1, -1):
                    if (C[k] < O[k]) if d == 1 else (C[k] > O[k]):
                        obs.append([cierre[i].value, H[k], L[k], d, i, 0]); break
        tr[i] = trend
        if i >= 2 and not np.isnan(a[i]):
            gap = 0.25 * a[i]
            if L[i] > H[i-2] and L[i] - H[i-2] >= gap: fvgs.append([cierre[i].value, L[i], H[i-2], 1, i, 1])
            if H[i] < L[i-2] and L[i-2] - H[i] >= gap: fvgs.append([cierre[i].value, L[i-2], H[i], -1, i, 1])
    Z = np.array(obs + fvgs, dtype=float).reshape(-1, 6)
    fin = np.full(len(Z), np.iinfo(np.int64).max, dtype=np.int64)
    cv = cierre.asi8
    for q, (_, top, bot, d, i, tipo) in enumerate(Z):
        i = int(i)
        if tipo == 0:
            m = np.where(C[i+1:] < bot)[0] if d == 1 else np.where(C[i+1:] > top)[0]
        else:
            m = np.where(L[i+1:] <= bot)[0] if d == 1 else np.where(H[i+1:] >= top)[0]
        if len(m): fin[q] = cv[i + 1 + m[0]]
    sw = np.array(swings, dtype=float).reshape(-1, 4)
    return dict(paso=paso, cierre=cv, tr=tr, atr=a,
                Z=dict(creado=Z[:, 0].astype(np.int64), top=Z[:, 1], bot=Z[:, 2], dir=Z[:, 3].astype(int),
                       tipo=Z[:, 5].astype(int), fin=fin),
                sw=dict(t=sw[:, 0].astype(np.int64), p=sw[:, 1], dir=sw[:, 2].astype(int), conf=sw[:, 3].astype(np.int64)))

def en_t(cierres, valores, t):
    k = np.searchsorted(cierres, t.value, side="right") - 1
    return valores[k] if k >= 0 else np.nan

# ============================== PRE-CÁLCULO POR ACTIVO ==============================
ACT = {}
for n, df in DATOS.items():
    t0 = time.time()
    m30 = remuestrear(df, "30min") if TF_ENTRADA == "30m" else remuestrear(df, "1h")
    h1, h4 = remuestrear(df, "1h"), remuestrear(df, "4h")
    paso_e = M30 if TF_ENTRADA == "30m" else H1
    E = {"e": estructura(m30, paso_e, 3), "1h": estructura(h1, H1, 3), "4h": estructura(h4, H4, 2)}
    dias = pd.Series(dia_trading(df.index), index=df.index)
    diario = df.groupby(dias.values).agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"),
                                         Close=("Close", "last"), velas=("Close", "size"))
    diario = diario[[pd.Timestamp(d).weekday() < 5 for d in diario.index]]
    diario["ema20"] = ema(diario.Close, 20)
    h1c = (h1.index + H1).asi8
    ACT[n] = dict(df=df, idx=df.index, iv=df.index.asi8, O=df.Open.values, H=df.High.values, L=df.Low.values,
                  C=df.Close.values, m30=m30, paso_e=paso_e, E=E, diario=diario, dias=dias.values,
                  h1c=h1c, e20=ema(h1.Close, 20).values, e50=ema(h1.Close, 50).values, rsi=rsi(h1.Close).values)
    print(f"   estructura {n}: {time.time() - t0:.1f}s")

REF = "EURUSD" if "EURUSD" in ACT else next(iter(ACT))
_vd = ACT[REF]["diario"]
DIAS = [d for d, v in zip(_vd.index, _vd.velas) if v >= 0.6 * _vd.velas.median()]
DIAS = [d for d in DIAS if pd.Timestamp(d) >= (DESDE + pd.Timedelta(days=21)).tz_localize(None)
        and cierre_dia(d) <= ACT[REF]["idx"][-1]]          # 3 semanas de calentamiento para indicadores
print(f"Días de trading a probar: {len(DIAS)} ({DIAS[0]} → {DIAS[-1]})")


# ===================================== LÓGICA DE LA ESTRATEGIA =====================================
def rango_idx(A, t_ini, t_fin):
    """Índices de velas base que CIERRAN dentro de [t_ini, t_fin]."""
    return np.searchsorted(A["iv"], t_ini.value), np.searchsorted(A["iv"], (t_fin - PASO).value, side="right")

def sesgo(A, t):
    i0, i1 = rango_idx(A, A["idx"][0], t)
    if i1 < 200: return 0, []
    E = A["E"]; precio = A["C"][i1 - 1]; pts, raz = 0, []
    iw = inicio_semana(t)
    if t - iw < pd.Timedelta(hours=30): iw -= pd.Timedelta(days=7)
    a, b = rango_idx(A, iw, t)
    r1 = en_t(A["h1c"], A["rsi"], t)
    if b > a:
        cambio = (precio / A["O"][a] - 1) * 100
        if cambio > 0.15: pts += 1; raz.append("semana+")
        elif cambio < -0.15: pts -= 1; raz.append("semana-")
        Hh, Ll = A["H"][a:b].max(), A["L"][a:b].min()
        pos = (precio - Ll) / (Hh - Ll) if Hh > Ll else 0.5
        if (pos > 0.85 or pos < 0.15) and (t.weekday() == 4 or r1 > 70 or r1 < 30): raz.append("extremo")
        elif pos > 0.66: pts += 1; raz.append("rango+")
        elif pos < 0.33: pts -= 1; raz.append("rango-")
    e20, e50 = en_t(A["h1c"], A["e20"], t), en_t(A["h1c"], A["e50"], t)
    if precio > e20 > e50: pts += 1; raz.append("ema+")
    elif precio < e20 < e50: pts -= 1; raz.append("ema-")
    for tf in ("1h", "4h"):
        tr = en_t(E[tf]["cierre"] , E[tf]["tr"], t)
        if not np.isnan(tr) and tr: pts += int(tr); raz.append(f"{tf}{'+' if tr > 0 else '-'}")
    hoy = (t.tz_convert(NY) + pd.Timedelta(hours=7)).date()
    dd = A["diario"]; dd = dd[dd.index < hoy]
    if len(dd) > 20:
        if dd.Close.iloc[-1] > dd.ema20.iloc[-1]: pts += 1; raz.append("D+")
        else: pts -= 1; raz.append("D-")
    if 55 <= r1 <= 70: pts += 1; raz.append("rsi+")
    elif 30 <= r1 <= 45: pts -= 1; raz.append("rsi-")
    return pts, raz

def niveles_liquidez(A, fecha):
    niv = {}; ds, lo = inicio_dia(fecha), hora_local(fecha, "08:00", LON)
    a, b = rango_idx(A, ds, lo)
    if b > a: niv["Asia H"], niv["Asia L"] = A["H"][a:b].max(), A["L"][a:b].min()
    dd = A["diario"]; prev = dd[dd.index < fecha]
    if len(prev): niv["PDH"], niv["PDL"] = prev.High.iloc[-1], prev.Low.iloc[-1]
    iw = inicio_semana(lo); a, b = rango_idx(A, iw - pd.Timedelta(days=7), iw)
    if b > a: niv["PWH"], niv["PWL"] = A["H"][a:b].max(), A["L"][a:b].min()
    a2 = np.searchsorted(A["iv"], iw.value)
    if a2 < len(A["O"]): niv["Ap. semanal"] = A["O"][a2]
    return niv

def niveles_sr(A, t, precio):
    S = A["E"]["1h"]["sw"]; m = (S["conf"] <= t.value) & (S["t"] >= (t - pd.Timedelta(days=10)).value)
    p = np.sort(S["p"][m]); a = en_t(A["E"]["1h"]["cierre"], A["E"]["1h"]["atr"], t)
    if len(p) < 2 or np.isnan(a): return [], []
    grupos, act = [], [p[0]]
    for x in p[1:]:
        if x - np.mean(act) <= 0.6 * a: act.append(x)
        else: grupos.append(act); act = [x]
    grupos.append(act)
    nv = [float(np.mean(g)) for g in grupos if len(g) >= 2]
    return sorted([x for x in nv if x > precio])[:3], sorted([x for x in nv if x < precio], reverse=True)[:3]

def objetivo(A, d, entrada, riesgo, t, niv, rr_min):
    cand = [v for v in niv.values() if (v - entrada) * d > 0]
    S = A["E"]["1h"]["sw"]
    m = (S["conf"] <= t.value) & (S["t"] >= (t - pd.Timedelta(days=5)).value) & (S["dir"] == d)
    cand += [p for p in S["p"][m] if (p - entrada) * d > 0]
    arr, aba = niveles_sr(A, t, entrada); cand += arr if d == 1 else aba
    for p in sorted(cand, key=lambda x: abs(x - entrada)):
        rr = abs(p - entrada) / riesgo
        if rr >= rr_min:
            return (p, "liquidez") if rr <= RR_MAX else (entrada + d * RR_OBJ * riesgo, "fijo")
    return entrada + d * RR_OBJ * riesgo, "fijo"

def simular(A, i_ent, d, sl, tp, t_cierre):
    a, b = i_ent, rango_idx(A, A["idx"][0], t_cierre)[1]
    if b <= a: return None
    p0 = A["O"][a]; riesgo = abs(p0 - sl)
    Hs, Ls = A["H"][a:b], A["L"][a:b]
    toca_sl = (Ls <= sl) if d == 1 else (Hs >= sl)
    toca_tp = (Hs >= tp) if d == 1 else (Ls <= tp)
    k_sl = np.argmax(toca_sl) if toca_sl.any() else 10**9
    k_tp = np.argmax(toca_tp) if toca_tp.any() else 10**9
    if k_sl == 10**9 and k_tp == 10**9:
        abierta = t_cierre > A["idx"][-1] + PASO
        salida, motivo, k = A["C"][b - 1], "abierta" if abierta else "cierre", b - 1 - a
    elif k_sl <= k_tp:                                # mismo bar → se asume SL (conservador)
        salida, motivo, k = sl, "SL", k_sl
    else:
        salida, motivo, k = tp, "TP", k_tp
    return dict(R=(salida - p0) * d / riesgo, motivo=motivo, t_salida=A["idx"][a + k], riesgo=riesgo, entrada=p0)

def zonas_tocadas(A, tf, t, d):
    Z = A["E"][tf]["Z"]; tv = t.value
    m = (Z["creado"] <= tv) & (Z["fin"] > tv) & (Z["dir"] == d)
    q = np.where(m)[0]
    return q[np.argsort(-Z["creado"][q])], Z

CACHE_SESGO, CACHE_NIV = {}, {}

def buscar_entrada(n, fecha, prm, hasta=None):
    A = ACT[n]; T0, T1 = ventana_operativa(fecha); TC = cierre_dia(fecha)
    if hasta is not None: T1 = min(T1, hasta)
    if (n, fecha) not in CACHE_SESGO: CACHE_SESGO[(n, fecha)] = sesgo(A, T0)
    pts, raz = CACHE_SESGO[(n, fecha)]
    d = 1 if pts >= prm["UMBRAL_SESGO"] else -1 if pts <= -prm["UMBRAL_SESGO"] else 0
    if d == 0: return None
    if (n, fecha) not in CACHE_NIV: CACHE_NIV[(n, fecha)] = niveles_liquidez(A, fecha)
    niv = CACHE_NIV[(n, fecha)]
    m30, pe, E = A["m30"], A["paso_e"], A["E"]
    i0 = np.searchsorted(m30.index.asi8, (T0 - pe).value, side="right")
    i1 = np.searchsorted(m30.index.asi8, (T1 - pe).value, side="right")
    MO, MH, ML, MC = (m30[c].values for c in ("Open", "High", "Low", "Close"))
    tfs = ("e", "1h") if TF_ENTRADA == "30m" else ("e",)
    for i in range(i0, i1):
        t = m30.index[i]; tc = t + pe
        if en_blackout(tc) or en_t(E["1h"]["cierre"], E["1h"]["tr"], tc) == -d: continue
        o, h, l, c = MO[i], MH[i], ML[i], MC[i]
        if not ((c > o) if d == 1 else (c < o)): continue
        a30 = en_t(E["e"]["cierre"], E["e"]["atr"], tc); a1 = en_t(E["1h"]["cierre"], E["1h"]["atr"], tc)
        if np.isnan(a30): continue
        setup = sl = None
        for k in (("Asia L", "PDL", "PWL") if d == 1 else ("Asia H", "PDH", "PWH")):
            lv = niv.get(k)
            if lv is not None and ((l < lv < c) if d == 1 else (h > lv > c)):
                setup, sl = f"Barrido {k}", (l - 0.2 * a30) if d == 1 else (h + 0.2 * a30); break
        if not setup:
            for tf in tfs:
                q, Z = zonas_tocadas(A, tf, t, d)
                for z in q:
                    top, bot = Z["top"][z], Z["bot"][z]; mid = (top + bot) / 2
                    if d == 1 and l <= top and c > mid:
                        setup, sl = f"Retroceso {'OB' if Z['tipo'][z] == 0 else 'FVG'}", min(bot, l) - 0.2 * a30; break
                    if d == -1 and h >= bot and c < mid:
                        setup, sl = f"Retroceso {'OB' if Z['tipo'][z] == 0 else 'FVG'}", max(top, h) + 0.2 * a30; break
                if setup: break
        if not setup:
            e = en_t(A["h1c"], A["e20"], tc)
            if not np.isnan(e) and ((l <= e < c) if d == 1 else (h >= e > c)):
                setup, sl = "Rebote EMA20 1h", (l - 0.3 * a30) if d == 1 else (h + 0.3 * a30)
        if not setup: continue
        i_ent = np.searchsorted(A["iv"], tc.value)
        if i_ent >= len(A["O"]): return None
        entrada = A["O"][i_ent]; riesgo = (entrada - sl) * d
        if riesgo < 0.4 * a30 or (not np.isnan(a1) and riesgo > 2.5 * a1): continue
        tp, tp_tipo = objetivo(A, d, entrada, riesgo, tc, niv, prm["RR_MIN"])
        r = simular(A, i_ent, d, sl, tp, TC)
        if r is None: return None
        r_op = simular(A, i_ent, -d, entrada + d * riesgo, entrada - (tp - entrada), TC)   # misma operación al revés
        costo = (SPREAD.get(n, 1.5) + 2 * SLIPPAGE_PIPS) * pip_de(n) / riesgo
        return dict(fecha=pd.Timestamp(fecha), activo=n, modo="confirmacion", dir=d, sesgo=pts, setup=setup, tp_tipo=tp_tipo,
                    t_entrada=tc, hora=tc.strftime("%H:%M"), sesion=sesion_de(tc), entrada=entrada, sl=sl, tp=tp,
                    rr_obj=abs(tp - entrada) / riesgo, riesgo_pips=riesgo / pip_de(n), motivo=r["motivo"],
                    t_salida=r["t_salida"], R_bruto=r["R"], costo_R=costo, R=r["R"] - costo,
                    R_opuesto=(r_op["R"] - costo) if r_op else -costo,
                    usd=USD_AL_COMPRAR.get(n, 0) * d)
    return None

# ============================== MODO "LÍMITE": ORDEN EN LA ZONA ==============================
def plan_limite(n, t, fecha, prm, pts=None):
    """Orden limitada en la zona/soporte más cercano a favor del sesgo, calculada con datos hasta t."""
    A = ACT[n]
    if pts is None: pts, _ = sesgo(A, t)
    d = 1 if pts >= prm["UMBRAL_SESGO"] else -1 if pts <= -prm["UMBRAL_SESGO"] else 0
    i1 = rango_idx(A, A["idx"][0], t)[1]
    if d == 0 or i1 < 10: return None
    precio = A["C"][i1 - 1]; a1 = en_t(A["E"]["1h"]["cierre"], A["E"]["1h"]["atr"], t)
    if np.isnan(a1): return None
    mejor = None
    for tf in (("1h", "e") if TF_ENTRADA == "30m" else ("e",)):
        q, Z = zonas_tocadas(A, tf, t, d)
        for z in q:
            ent, lim = (Z["top"][z], Z["bot"][z]) if d == 1 else (Z["bot"][z], Z["top"][z])
            if (precio - ent) * d > 0 and abs(precio - ent) < 3 * a1:
                if mejor is None or (ent - mejor[0]) * d > 0:
                    mejor = (ent, lim, f"{'OB' if Z['tipo'][z] == 0 else 'FVG'} {'30m' if tf == 'e' else tf}")
    if mejor is None:
        arr, aba = niveles_sr(A, t, precio)
        lv = (aba if d == 1 else arr)
        if lv and abs(precio - lv[0]) < 3 * a1:
            mejor = (lv[0], lv[0], "soporte 1h" if d == 1 else "resistencia 1h")
    if mejor is None: return None
    ent, lim, zona = mejor
    sl = lim - d * 0.3 * a1 if lim != ent else ent - d * 1.0 * a1
    riesgo = abs(ent - sl)
    if riesgo <= 0: return None
    if (fecha_k := (n, fecha)) not in CACHE_NIV: CACHE_NIV[fecha_k] = niveles_liquidez(A, fecha)
    tp, tp_tipo = objetivo(A, d, ent, riesgo, t, CACHE_NIV[fecha_k], prm["RR_MIN"])
    return dict(dir=d, entrada=ent, sl=sl, tp=tp, zona=zona, tp_tipo=tp_tipo, sesgo=pts, precio=precio, riesgo=riesgo)

def simular_limite(n, p, T0, T1, TC):
    """Llenado si el precio toca la entrada entre T0 y T1 (antes de tocar el TP). Luego SL/TP hasta el cierre del día."""
    A = ACT[n]; d = p["dir"]
    a = np.searchsorted(A["iv"], T0.value); b = rango_idx(A, A["idx"][0], T1)[1]
    for k in range(a, b):
        if (A["L"][k] <= p["entrada"]) if d == 1 else (A["H"][k] >= p["entrada"]):
            precio_ent = min(A["O"][k], p["entrada"]) if d == 1 else max(A["O"][k], p["entrada"])
            fin = rango_idx(A, A["idx"][0], TC)[1]
            Hs, Ls = A["H"][k:fin], A["L"][k:fin]
            toca_sl = (Ls <= p["sl"]) if d == 1 else (Hs >= p["sl"])
            toca_tp = (Hs >= p["tp"]) if d == 1 else (Ls <= p["tp"]); toca_tp[0] = False   # TP no cuenta en la vela de llenado
            ksl = np.argmax(toca_sl) if toca_sl.any() else 10**9
            ktp = np.argmax(toca_tp) if toca_tp.any() else 10**9
            if ksl == 10**9 and ktp == 10**9:
                abierta = TC > A["idx"][-1] + PASO
                sal, mot, kk = A["C"][fin - 1], "abierta" if abierta else "cierre", fin - 1 - k
            elif ksl <= ktp: sal, mot, kk = p["sl"], "SL", ksl
            else: sal, mot, kk = p["tp"], "TP", ktp
            # misma operación al revés (para el test de permutación)
            sl_o, tp_o = precio_ent + d * p["riesgo"], precio_ent - (p["tp"] - precio_ent)
            so = (Hs >= sl_o) if d == 1 else (Ls <= sl_o); to = (Ls <= tp_o) if d == 1 else (Hs >= tp_o); to[0] = False
            k1 = np.argmax(so) if so.any() else 10**9; k2 = np.argmax(to) if to.any() else 10**9
            r_op = ((A["C"][fin - 1] - precio_ent) * -d if k1 == k2 == 10**9 else (-p["riesgo"] if k1 <= k2 else abs(tp_o - precio_ent)))
            riesgo = abs(precio_ent - p["sl"])
            return dict(t_entrada=A["idx"][k], entrada=precio_ent, motivo=mot, t_salida=A["idx"][k + kk],
                        R_bruto=(sal - precio_ent) * d / riesgo, R_op_bruto=r_op / riesgo, riesgo=riesgo)
        if (A["H"][k] >= p["tp"]) if d == 1 else (A["L"][k] <= p["tp"]):
            return None                                             # se fue al objetivo sin llenarse: cancelada
    return None

def entrada_limite(n, fecha, prm):
    A = ACT[n]; T0, T1 = ventana_operativa(fecha); TC = cierre_dia(fecha)
    if (n, fecha) not in CACHE_SESGO: CACHE_SESGO[(n, fecha)] = sesgo(A, T0)
    p = plan_limite(n, T0, fecha, prm, CACHE_SESGO[(n, fecha)][0])
    if p is None: return None
    r = simular_limite(n, p, T0, T1, TC)
    if r is None: return None
    costo = (SPREAD.get(n, 1.5) + 2 * SLIPPAGE_PIPS) * pip_de(n) / r["riesgo"]
    return dict(fecha=pd.Timestamp(fecha), activo=n, modo="limite", dir=p["dir"], sesgo=p["sesgo"], setup=f"Límite en {p['zona']}",
                tp_tipo=p["tp_tipo"], t_entrada=r["t_entrada"], hora=r["t_entrada"].strftime("%H:%M"),
                sesion=sesion_de(r["t_entrada"]), entrada=r["entrada"], sl=p["sl"], tp=p["tp"],
                rr_obj=abs(p["tp"] - r["entrada"]) / r["riesgo"], riesgo_pips=r["riesgo"] / pip_de(n), motivo=r["motivo"],
                t_salida=r["t_salida"], R_bruto=r["R_bruto"], costo_R=costo, R=r["R_bruto"] - costo,
                R_opuesto=r["R_op_bruto"] - costo, usd=USD_AL_COMPRAR.get(n, 0) * p["dir"])


# ======================= HERRAMIENTAS DE TRADER: FIBONACCI, PIVOTES, RSI, TENDENCIAS =======================
FIB_RET = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
FIB_EXT = [-0.272, -0.618]                                   # extensiones más allá del extremo del impulso
FIB_COL = {0: "#787b86", 0.236: "#f23645", 0.382: "#ff9800", 0.5: "#4caf50", 0.618: "#089981", 0.786: "#00bcd4",
           1: "#787b86", -0.272: "#2962ff", -0.618: "#9c27b0"}
H_NS = 3_600_000_000_000

def impulso_fibo(A, t, d):
    """Último impulso de 1h a favor de d (d=1: mínimo → máximo). Válido si el precio retrocede sin romper su origen."""
    if d == 0: return None
    S = A["E"]["1h"]["sw"]; m = S["conf"] <= t.value
    ts, ps, ds = S["t"][m], S["p"][m], S["dir"][m]
    ext = np.where(ds == d)[0]
    if not len(ext): return None
    k = ext[-1]; t1, p1 = ts[k], ps[k]
    org = np.where((ds == -d) & (ts < t1) & (ts >= t1 - 80 * H_NS))[0]
    if not len(org): return None
    j = org[np.argmin(ps[org])] if d == 1 else org[np.argmax(ps[org])]
    t0, p0 = ts[j], ps[j]
    a1 = en_t(A["E"]["1h"]["cierre"], A["E"]["1h"]["atr"], t)
    rango = abs(p1 - p0)
    if np.isnan(a1) or rango < 2 * a1: return None
    i0 = np.searchsorted(A["iv"], t1); i1 = rango_idx(A, A["idx"][0], t)[1]
    if i1 > i0:
        if d == 1 and (A["L"][i0:i1].min() <= p0 or A["H"][i0:i1].max() > p1): return None
        if d == -1 and (A["H"][i0:i1].max() >= p0 or A["L"][i0:i1].min() < p1): return None
    return dict(d=d, t0=t0, p0=p0, t1=t1, p1=p1, rango=rango)

def nivel_fibo(imp, r):
    return imp["p1"] - imp["d"] * r * imp["rango"]

def plan_fibo(n, t, fecha, prm, pts=None):
    """Orden limitada en el 0,618 del último impulso a favor del sesgo; SL detrás del origen (1,0); TP en el extremo (0) o 1,272."""
    A = ACT[n]
    if pts is None: pts, _ = sesgo(A, t)
    d = 1 if pts >= prm["UMBRAL_SESGO"] else -1 if pts <= -prm["UMBRAL_SESGO"] else 0
    imp = impulso_fibo(A, t, d)
    if imp is None: return None
    i1 = rango_idx(A, A["idx"][0], t)[1]; precio = A["C"][i1 - 1]
    a1 = en_t(A["E"]["1h"]["cierre"], A["E"]["1h"]["atr"], t)
    ent = nivel_fibo(imp, 0.618); sl = nivel_fibo(imp, 1.0) - d * 0.15 * a1
    if (precio - ent) * d <= 0: return None                   # el precio ya pasó el 0,618
    riesgo = abs(ent - sl); tp, tp_tipo = nivel_fibo(imp, 0), "Fibo 0 (extremo del impulso)"
    if abs(tp - ent) / riesgo < prm["RR_MIN"]:
        tp, tp_tipo = nivel_fibo(imp, -0.272), "extensión 1,272"
    return dict(dir=d, entrada=ent, sl=sl, tp=tp, zona="Fibo 0,618", tp_tipo=tp_tipo, sesgo=pts, precio=precio,
                riesgo=riesgo, imp=imp)

def entrada_plan(n, fecha, prm, fn_plan, modo):
    A = ACT[n]; T0, T1 = ventana_operativa(fecha); TC = cierre_dia(fecha)
    if (n, fecha) not in CACHE_SESGO: CACHE_SESGO[(n, fecha)] = sesgo(A, T0)
    p = fn_plan(n, T0, fecha, prm, CACHE_SESGO[(n, fecha)][0])
    if p is None: return None
    r = simular_limite(n, p, T0, T1, TC)
    if r is None: return None
    costo = (SPREAD.get(n, 1.5) + 2 * SLIPPAGE_PIPS) * pip_de(n) / r["riesgo"]
    return dict(fecha=pd.Timestamp(fecha), activo=n, modo=modo, dir=p["dir"], sesgo=p["sesgo"], setup=f"Límite en {p['zona']}",
                tp_tipo=p["tp_tipo"], t_entrada=r["t_entrada"], hora=r["t_entrada"].strftime("%H:%M"),
                sesion=sesion_de(r["t_entrada"]), entrada=r["entrada"], sl=p["sl"], tp=p["tp"],
                rr_obj=abs(p["tp"] - r["entrada"]) / r["riesgo"], riesgo_pips=r["riesgo"] / pip_de(n), motivo=r["motivo"],
                t_salida=r["t_salida"], R_bruto=r["R_bruto"], costo_R=costo, R=r["R_bruto"] - costo,
                R_opuesto=r["R_op_bruto"] - costo, usd=USD_AL_COMPRAR.get(n, 0) * p["dir"])

def pivotes(A, fecha):
    prev = A["diario"][A["diario"].index < fecha]
    if not len(prev): return {}
    H, L, C = prev.High.iloc[-1], prev.Low.iloc[-1], prev.Close.iloc[-1]; P = (H + L + C) / 3
    return {"P": P, "R1": 2 * P - L, "R2": P + (H - L), "S1": 2 * P - H, "S2": P - (H - L)}

def divergencia_rsi(A, t):
    """Divergencia entre los dos últimos swings de 1h y el RSI 1h (solo si el último swing es reciente)."""
    S = A["E"]["1h"]["sw"]; m = S["conf"] <= t.value
    h1o = A["h1c"] - H_NS
    for d, nombre in ((-1, "alcista"), (1, "bajista")):
        q = np.where(m & (S["dir"] == d))[0]
        if len(q) < 2 or t.value - S["t"][q[-1]] > 30 * H_NS: continue
        a, b = q[-2], q[-1]
        ra = A["rsi"][min(np.searchsorted(h1o, S["t"][a]), len(h1o) - 1)]
        rb = A["rsi"][min(np.searchsorted(h1o, S["t"][b]), len(h1o) - 1)]
        if d == -1 and S["p"][b] < S["p"][a] and rb > ra + 2:
            return nombre, (S["t"][a], S["p"][a], ra), (S["t"][b], S["p"][b], rb)
        if d == 1 and S["p"][b] > S["p"][a] and rb < ra - 2:
            return nombre, (S["t"][a], S["p"][a], ra), (S["t"][b], S["p"][b], rb)
    return None

def lineas_tendencia(A, t):
    """Soporte = recta por los dos últimos mínimos ascendentes; resistencia = por los dos últimos máximos descendentes."""
    S = A["E"]["1h"]["sw"]; m = (S["conf"] <= t.value) & (S["t"] >= t.value - 10 * 24 * H_NS); out = []
    for d, nombre in ((-1, "soporte"), (1, "resistencia")):
        q = np.where(m & (S["dir"] == d))[0]
        if len(q) >= 2:
            a, b = q[-2], q[-1]
            if (d == -1 and S["p"][b] > S["p"][a]) or (d == 1 and S["p"][b] < S["p"][a]):
                pend = (S["p"][b] - S["p"][a]) / (S["t"][b] - S["t"][a])
                out.append(dict(tipo=nombre, t0=S["t"][a], p0=S["p"][a], pend=pend,
                                valor=lambda tt, a=a, pend=pend: S["p"][a] + pend * (tt - S["t"][a])))
    return out

def confluencias(n, t, fecha):
    """Agrupa niveles de varias herramientas que coinciden (± 0,35 ATR de 1h)."""
    A = ACT[n]; a1 = en_t(A["E"]["1h"]["cierre"], A["E"]["1h"]["atr"], t)
    i1 = rango_idx(A, A["idx"][0], t)[1]; precio = A["C"][i1 - 1]
    niv = []
    pts, _ = sesgo(A, t); d = 1 if pts > 0 else -1 if pts < 0 else 0
    imp = impulso_fibo(A, t, d) or impulso_fibo(A, t, -d)
    if imp:
        niv += [(nivel_fibo(imp, r), f"Fibo {r:g}".replace(".", ",")) for r in FIB_RET[1:-1] + FIB_EXT]
    niv += [(v, f"Pivote {k}") for k, v in pivotes(A, fecha).items()]
    niv += [(v, k) for k, v in niveles_liquidez(A, fecha).items()]
    arr, aba = niveles_sr(A, t, precio); niv += [(v, "S/R 1h") for v in arr + aba]
    Z = A["E"]["1h"]["Z"]; q = np.where((Z["creado"] <= t.value) & (Z["fin"] > t.value))[0]
    niv += [((Z["top"][z] + Z["bot"][z]) / 2, ("OB" if Z["tipo"][z] == 0 else "FVG") + " 1h") for z in q]
    niv += [(en_t(A["h1c"], A["e20"], t), "EMA20 1h"), (en_t(A["h1c"], A["e50"], t), "EMA50 1h")]
    niv = sorted([x for x in niv if not np.isnan(x[0]) and abs(x[0] - precio) < 6 * a1])
    grupos, act = [], []
    for v, k in niv:
        if act and v - act[0][0] > 0.35 * a1:
            grupos.append(act); act = []
        act.append((v, k))
    if act: grupos.append(act)
    out = []
    for g in grupos:
        herr = sorted({k for _, k in g})
        if len(herr) >= 2:
            out.append(dict(precio=float(np.mean([v for v, _ in g])), n=len(herr), herramientas=herr))
    arriba = sorted([c for c in out if c["precio"] > precio], key=lambda c: c["precio"])[:3]
    abajo = sorted([c for c in out if c["precio"] < precio], key=lambda c: -c["precio"])[:3]
    return dict(arriba=arriba, abajo=abajo, imp=imp, precio=precio, a1=a1)


# ===================================== VALIDACIÓN =====================================
PRM = {"UMBRAL_SESGO": UMBRAL_SESGO, "RR_MIN": RR_MIN}
MODOS = {"confirmacion": lambda n, f: buscar_entrada(n, f, PRM),
         "limite": lambda n, f: entrada_plan(n, f, PRM, plan_limite, "limite"),
         "fibo": lambda n, f: entrada_plan(n, f, PRM, plan_fibo, "fibo")}
CORTE = int(len(DIAS) * (1 - OOS_FRACCION)); DIAS_IS, DIAS_OOS = DIAS[:CORTE], DIAS[CORTE:]
F_CORTE = pd.Timestamp(DIAS_OOS[0])

print("\nValidando (todas las operaciones posibles, sin filtro de exposición)...")
t0 = time.time(); TODAS = []
for j, f in enumerate(DIAS):
    for n in ACT:
        for m, fn in MODOS.items():
            x = fn(n, f)
            if x: TODAS.append(x)
    if j % 100 == 0: print(f"   {j}/{len(DIAS)} días")
TODAS = pd.DataFrame(TODAS)
print(f"   {len(TODAS)} operaciones candidatas en {time.time() - t0:.0f}s")

def metricas(t, dias):
    if len(t) == 0: return dict(n=0)
    t = t.sort_values("t_entrada"); R = t.R.values; gan, per = R[R > 0], R[R <= 0]
    cap = CAPITAL * np.r_[1, np.cumprod(1 + RIESGO_PCT / 100 * R)]
    racha = mx = 0
    for r in R:
        racha = racha + 1 if r <= 0 else 0; mx = max(mx, racha)
    pdia = t.groupby("fecha").R.sum().reindex(pd.to_datetime(dias), fill_value=0)
    return dict(n=len(t), win=(R > 0).mean() * 100, exp=R.mean(), pf=gan.sum() / abs(per.sum()) if per.sum() else np.inf,
                total=R.sum(), dd_pct=(cap / np.maximum.accumulate(cap) - 1).min() * 100, racha=mx,
                por_mes=len(t) / max(len(dias) / 21, 1), ret_pct=(cap[-1] / CAPITAL - 1) * 100,
                sharpe=pdia.mean() / pdia.std() * np.sqrt(252) if pdia.std() > 0 else 0)

def filtro_exposicion(t):
    out = []
    for _, g in t.sort_values(["fecha", "t_entrada"]).groupby("fecha"):
        lado, usados = {1: 0, -1: 0}, set()
        for _, x in g.sort_values(["t_entrada", "sesgo"], key=lambda c: c if c.name == "t_entrada" else -c.abs()).iterrows():
            if x.activo in usados: continue                         # una operación por activo y día
            if x.usd and lado[x.usd] >= MAX_USD_LADO: continue
            if x.usd: lado[x.usd] += 1
            usados.add(x.activo); out.append(x)
    return pd.DataFrame(out)

# 1) aprobación por activo y modo usando SOLO in-sample
APROB = []
TABLA_APROB = []
for n in ACT:
    for m in MODOS:
        t = TODAS[(TODAS.activo == n) & (TODAS.modo == m)] if len(TODAS) else TODAS
        mi = metricas(t[t.fecha < F_CORTE], DIAS_IS) if len(t) else dict(n=0)
        mo = metricas(t[t.fecha >= F_CORTE], DIAS_OOS) if len(t) else dict(n=0)
        ok = mi.get("n", 0) >= MIN_IS and mi.get("exp", -1) > EXP_MIN_IS and mi.get("pf", 0) > 1.1
        if ok: APROB.append((n, m))
        TABLA_APROB.append({"Activo": n, "Modo": m, "Ops IS": mi.get("n", 0), "Exp. IS": mi.get("exp", np.nan),
                            "PF IS": mi.get("pf", np.nan), "Ops OOS": mo.get("n", 0), "Exp. OOS": mo.get("exp", np.nan),
                            "Aprobado": "SÍ" if ok else "no"})
TABLA_APROB = pd.DataFrame(TABLA_APROB)

# 2) cartera aprobada, juzgada fuera de muestra
if APROB and len(TODAS):
    mask = TODAS.apply(lambda x: (x.activo, x.modo) in APROB, axis=1)
    CARTERA = filtro_exposicion(TODAS[mask])
else:
    CARTERA = pd.DataFrame(columns=TODAS.columns if len(TODAS) else ["fecha", "R"])
C_IS = CARTERA[CARTERA.fecha < F_CORTE] if len(CARTERA) else CARTERA
C_OOS = CARTERA[CARTERA.fecha >= F_CORTE] if len(CARTERA) else CARTERA
M_IS, M_OOS = metricas(C_IS, DIAS_IS), metricas(C_OOS, DIAS_OOS)

def permutacion(t):
    if len(t) == 0: return np.nan, np.array([])
    rng = np.random.default_rng(7); R, Ro = t.R.values, t.R_opuesto.values
    sims = np.array([np.where(rng.random(len(R)) < 0.5, R, Ro).sum() for _ in range(N_PERMUT)])
    return (np.sum(sims >= R.sum()) + 1) / (N_PERMUT + 1), sims

def bootstrap_ic(t):
    if len(t) < 5: return np.nan, np.nan
    rng = np.random.default_rng(11); R = t.R.values
    m = np.array([rng.choice(R, len(R)).mean() for _ in range(2000)])
    return np.percentile(m, 2.5), np.percentile(m, 97.5)

P_VAL, SIMS = permutacion(C_OOS); IC = bootstrap_ic(C_OOS)
if not APROB:
    VER = ("SIN VENTAJA", "Ningún activo/modo pasó la validación in-sample. Las órdenes se muestran solo como práctica en demo.")
elif M_OOS.get("n", 0) < MIN_TRADES:
    VER = ("INSUFICIENTE", f"Solo {M_OOS.get('n', 0)} operaciones fuera de muestra: no alcanza para concluir. Operar solo en demo.")
elif M_OOS["exp"] > 0.05 and P_VAL < 0.05 and IC[0] > 0:
    VER = ("VENTAJA CON EVIDENCIA", "La cartera aprobada gana fuera de muestra, el intervalo de confianza no incluye el cero y "
           "el azar casi nunca la iguala. Apta para real con riesgo bajo, idealmente tras 1–3 meses de demo.")
elif M_OOS["exp"] > 0:
    VER = ("POSITIVO PERO NO CONCLUYENTE", "Gana fuera de muestra pero podría ser suerte (p ≥ 0,05 o el intervalo incluye el cero). "
           "Operar solo en demo hasta acumular evidencia.")
else:
    VER = ("SIN VENTAJA", "La cartera aprobada pierde fuera de muestra después de costos. Operar solo en demo.")
APTA = VER[0] == "VENTAJA CON EVIDENCIA"
ETIQUETA = "APTA PARA REAL (riesgo bajo)" if APTA else "SOLO DEMO"


# ===================================== ÓRDENES DE HOY =====================================
CONTRATO = {"XAUUSD": 100, "BTCUSD": 1}
if ACC is not None:
    CUENTA_USD = ACC.balance
def lotes(n, entrada, riesgo, d=1):
    if MT5 is not None and n in SIMB:                      # cálculo exacto con las especificaciones de tu cuenta
        info = MT5_INFO[n]
        perdida = MT5.order_calc_profit(MT5.ORDER_TYPE_BUY if d == 1 else MT5.ORDER_TYPE_SELL, SIMB[n], 1.0,
                                        entrada, entrada - d * riesgo)
        if perdida:
            paso_v = info.volume_step or 0.01
            lot = np.floor(CUENTA_USD * RIESGO_PCT / 100 / abs(perdida) / paso_v) * paso_v
            return f"{lot:.2f}" if lot >= info.volume_min else f"{info.volume_min:.2f} (supera el riesgo)"
    por_lote = riesgo * CONTRATO.get(n, 100_000) / (entrada if n.startswith("USD") else 1)
    lot = np.floor(CUENTA_USD * RIESGO_PCT / 100 / por_lote * 100) / 100 if por_lote > 0 else 0
    return f"{lot:.2f}" if lot >= 0.01 else "0.01 (supera el riesgo)"

AHORA = max(A["idx"][-1] for A in ACT.values()) + PASO
_hoy = (AHORA.tz_convert(NY) + pd.Timedelta(hours=7)).date()
def _sig_habil(d): return pd.bdate_range(pd.Timestamp(d) + pd.Timedelta(days=1), periods=1)[0].date()
if pd.Timestamp(_hoy).weekday() >= 5:
    _hoy = _sig_habil(_hoy - pd.Timedelta(days=1)) if pd.Timestamp(_hoy).weekday() == 6 else _sig_habil(_hoy)
T0_HOY, T1_HOY = ventana_operativa(_hoy)
if AHORA >= T1_HOY:
    _hoy = _sig_habil(_hoy); T0_HOY, T1_HOY = ventana_operativa(_hoy)
FASE = "antes" if AHORA < T0_HOY else "ventana"
DIA_OP = _hoy
retraso = HOY - AHORA
AVISO_DATOS = (f"Los datos más recientes son de {fmt_hora(AHORA)} ({retraso.total_seconds() / 60:.0f} min de retraso)."
               if retraso > pd.Timedelta(minutes=45) and pd.Timestamp(_hoy).weekday() < 5 and FASE == "ventana" else "")

ORDENES = []
for n in ACT:
    A = ACT[n]; pip = pip_de(n); precio = A["C"][-1]
    if MT5 is not None and n in SIMB and (tk := MT5.symbol_info_tick(SIMB[n])) is not None and tk.bid:
        precio = tk.bid                                    # precio en vivo de tu bróker
    for m in MODOS:
        if SOLO_APROBADOS and (n, m) not in APROB: continue
        o = dict(Activo=n, Modo=m, Precio=precio, Validado="SÍ" if (n, m) in APROB else "no")
        if m in ("limite", "fibo"):
            t_plan = T0_HOY if FASE == "ventana" else AHORA
            p = (plan_limite if m == "limite" else plan_fibo)(n, t_plan, DIA_OP, PRM)
            if p is None:
                o.update(Estado="SIN ORDEN", Nota="sin sesgo suficiente o sin zona cercana" if m == "limite"
                         else "sin impulso de Fibonacci válido a favor del sesgo"); ORDENES.append(o); continue
            o.update(dir=p["dir"], sesgo=p["sesgo"], Entrada=p["entrada"], SL=p["sl"], TP=p["tp"], riesgo=p["riesgo"], imp=p.get("imp"))
            if FASE == "antes":
                o.update(Estado="PRELIMINAR", Orden=f"{'BUY' if p['dir'] == 1 else 'SELL'} LIMIT",
                         Nota=f"Zona {p['zona']}. Se confirma a la apertura de Londres ({fmt_hora(T0_HOY)}): volver a ejecutar ahí.")
            else:
                r = simular_limite(n, p, T0_HOY, min(T1_HOY, AHORA), cierre_dia(DIA_OP))
                if r is None:
                    tocado_tp = ((A["H"][np.searchsorted(A["iv"], T0_HOY.value):] >= p["tp"]).any() if p["dir"] == 1
                                 else (A["L"][np.searchsorted(A["iv"], T0_HOY.value):] <= p["tp"]).any())
                    if tocado_tp:
                        o.update(Estado="CANCELADA", Orden="—", Nota="el precio llegó al objetivo sin llenar la orden")
                    else:
                        o.update(Estado="PENDIENTE", Orden=f"{'BUY' if p['dir'] == 1 else 'SELL'} LIMIT",
                                 Nota=f"Zona {p['zona']}, TP en {p['tp_tipo']}. Cancelar a las {T1_HOY:%H:%M} si no se llena.")
                elif r["motivo"] == "abierta":
                    o.update(Estado="EN CURSO", Orden="(ya llenada)", Nota=f"Se llenó a las {r['t_entrada']:%H:%M}; mantener SL y TP.")
                else:
                    o.update(Estado="CERRADA", Orden="—", Nota=f"Se llenó a las {r['t_entrada']:%H:%M} y cerró por {r['motivo']} ({r['R_bruto']:+.2f}R).")
        else:
            if FASE == "antes":
                pts, _ = sesgo(A, AHORA); d = 1 if pts >= UMBRAL_SESGO else -1 if pts <= -UMBRAL_SESGO else 0
                o.update(Estado="VIGILAR" if d else "SIN ORDEN", dir=d, sesgo=pts, Orden="—",
                         Nota=(f"Sesgo {'comprador' if d == 1 else 'vendedor'} ({pts:+d}). Desde las {T0_HOY:%H:%M} esperar vela de "
                               f"{TF_ENTRADA} que reaccione en zona, barrido de liquidez o EMA20 1h; volver a ejecutar.") if d else
                              f"sesgo insuficiente ({pts:+d})")
            else:
                x = buscar_entrada(n, DIA_OP, PRM, hasta=AHORA)
                if x is None:
                    pts, _ = CACHE_SESGO.get((n, DIA_OP), (0, []))
                    d = 1 if pts >= UMBRAL_SESGO else -1 if pts <= -UMBRAL_SESGO else 0
                    o.update(Estado="VIGILAR" if d else "SIN ORDEN", dir=d, sesgo=pts, Orden="—",
                             Nota=(f"Sesgo {'comprador' if d == 1 else 'vendedor'} ({pts:+d}); aún sin vela de confirmación. "
                                   f"Volver a ejecutar cada 30 min hasta las {T1_HOY:%H:%M}.") if d else f"sesgo insuficiente ({pts:+d})")
                else:
                    riesgo = abs(x["entrada"] - x["sl"]); dist = (precio - x["entrada"]) * x["dir"]
                    o.update(dir=x["dir"], sesgo=x["sesgo"], Entrada=x["entrada"], SL=x["sl"], TP=x["tp"], riesgo=riesgo)
                    if x["motivo"] != "abierta":
                        o.update(Estado="CERRADA", Orden="—", Nota=f"Señal de {x['setup']} a las {x['hora']}; cerró por {x['motivo']} ({x['R_bruto']:+.2f}R).")
                    elif -0.5 * riesgo < dist <= 0.3 * riesgo:
                        o.update(Estado="ENTRAR AHORA", Orden=f"{'BUY' if x['dir'] == 1 else 'SELL'} MARKET",
                                 Nota=f"Señal de {x['setup']} a las {x['hora']}; el precio sigue cerca de la entrada.")
                    else:
                        o.update(Estado="NO PERSEGUIR", Orden="—",
                                 Nota=f"Señal de {x['setup']} a las {x['hora']}, pero el precio ya se movió {dist / riesgo:+.1f}R.")
        ORDENES.append(o)

# exposición: una por activo y máx. MAX_USD_LADO por lado del USD (prioridad: sesgo más fuerte)
ACCIONABLES = ("ENTRAR AHORA", "PENDIENTE", "PRELIMINAR", "EN CURSO")
lado, usados = {1: 0, -1: 0}, set()
for o in sorted(ORDENES, key=lambda o: (o["Estado"] not in ACCIONABLES, o["Estado"] != "EN CURSO", o.get("Validado") != "SÍ", -abs(o.get("sesgo", 0)))):
    if o["Estado"] not in ACCIONABLES: continue
    u = USD_AL_COMPRAR.get(o["Activo"], 0) * o["dir"]
    if o["Activo"] in usados or (u and lado[u] >= MAX_USD_LADO):
        o["Nota"] = ("ya hay una orden en este activo. " if o["Activo"] in usados else
                     f"exposición: ya hay {MAX_USD_LADO} órdenes del mismo lado del USD. ") + o["Nota"]
        o["Estado"] = "OMITIDA"; continue
    usados.add(o["Activo"])
    if u: lado[u] += 1
for o in ORDENES:
    if o.get("riesgo"):
        o["RR"] = abs(o["TP"] - o["Entrada"]) / o["riesgo"]; o["SL pips"] = o["riesgo"] / pip_de(o["Activo"])
        o["Lotes"] = lotes(o["Activo"], o["Entrada"], o["riesgo"], o.get("dir", 1) or 1)
_ord = {"ENTRAR AHORA": 0, "EN CURSO": 1, "PENDIENTE": 2, "PRELIMINAR": 3, "VIGILAR": 4, "NO PERSEGUIR": 5,
        "OMITIDA": 6, "CERRADA": 7, "CANCELADA": 8, "SIN ORDEN": 9}
ORDENES.sort(key=lambda o: (_ord.get(o["Estado"], 9), o.get("Validado") != "SÍ", -abs(o.get("sesgo", 0))))
DEC = {"XAUUSD": 2, "BTCUSD": 0, "USDJPY": 3}
fp = lambda n, x: "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{DEC.get(n, 5)}f}"
CAB = ["Activo", "Modo", "Validado", "Estado", "Orden", "Sesgo", "Precio", "Entrada", "SL", "TP", "RR", "SL pips", "Lotes"]
def filas_ordenes():
    return [[o["Activo"] + (f" ({SIMB[o['Activo']]})" if SIMB.get(o["Activo"], o["Activo"]) != o["Activo"] else ""), o["Modo"], o.get("Validado", "—"), o["Estado"], o.get("Orden", "—"), f"{o['sesgo']:+d}" if "sesgo" in o else "—",
             fp(o["Activo"], o["Precio"]), fp(o["Activo"], o.get("Entrada")), fp(o["Activo"], o.get("SL")),
             fp(o["Activo"], o.get("TP")), f"{o['RR']:.1f}R" if o.get("RR") else "—",
             f"{o['SL pips']:,.0f}" if o.get("SL pips") else "—", o.get("Lotes", "—")] for o in ORDENES]


# ===================================== GRÁFICOS =====================================
TV = dict(bg="#131722", grid="#1e222d", txt="#d1d4dc", sub="#787b86", up="#26a69a", dn="#ef5350", az="#2962ff", am="#f5c542")
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8})
def _fig(size):
    fig, ax = plt.subplots(figsize=size, dpi=130); fig.patch.set_facecolor(TV["bg"]); ax.set_facecolor(TV["bg"])
    ax.tick_params(colors=TV["sub"]); ax.grid(color=TV["grid"], lw=0.6); [s.set_color(TV["grid"]) for s in ax.spines.values()]
    return fig, ax
def _save(fig, nombre):
    fig.tight_layout(); r = f"{CARPETA}/{nombre}.png"; fig.savefig(r, facecolor=TV["bg"]); plt.close(fig); return r

def grafico_analisis(n, o=None):
    """Gráfico 1h estilo TradingView con Fibonacci, pivotes, tendencias, Bollinger, zonas, confluencias, RSI y la orden."""
    A = ACT[n]; h1 = A["df"].resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    h1 = h1[h1.index + H1 <= AHORA]
    m = h1.iloc[-130:]; N = len(m); x = np.arange(N); iv = m.index.asi8
    X = lambda tv: np.interp(tv, iv, x, left=0, right=N - 1) if tv <= iv[-1] else N - 1 + (tv - iv[-1]) / H_NS
    CF = confluencias(n, AHORA, DIA_OP); precio = CF["precio"]
    fig = plt.figure(figsize=(13, 6.6), dpi=130); fig.patch.set_facecolor(TV["bg"])
    gs = fig.add_gridspec(2, 1, height_ratios=[4.2, 1], hspace=0.04)
    ax, ar = fig.add_subplot(gs[0]), fig.add_subplot(gs[1], sharex=None)
    for a_ in (ax, ar):
        a_.set_facecolor(TV["bg"]); a_.tick_params(colors=TV["sub"]); a_.grid(color=TV["grid"], lw=0.6)
        [sp.set_color(TV["grid"]) for sp in a_.spines.values()]
    lo, hi = m.Low.min(), m.High.max(); rng = hi - lo
    ylo, yhi = lo - rng * 0.05, hi + rng * 0.05
    if o and o.get("Entrada") is not None:
        ylo, yhi = min(ylo, o["SL"], o["TP"]) - rng * 0.03, max(yhi, o["SL"], o["TP"]) + rng * 0.03
    xr = N + 22
    # Fibonacci con bandas de color
    imp = (o or {}).get("imp") or CF["imp"]
    if imp:
        xf0 = X(imp["t0"]); niveles = sorted(FIB_RET + FIB_EXT)
        vals = {r: nivel_fibo(imp, r) for r in niveles}
        for r1, r2 in zip(FIB_RET[:-1], FIB_RET[1:]):
            ax.fill_between([xf0, xr], vals[r1], vals[r2], color=FIB_COL[r2], alpha=0.07, lw=0)
        for r in niveles:
            v = vals[r]
            if ylo - rng * 0.3 < v < yhi + rng * 0.3:
                ax.hlines(v, xf0, xr, colors=FIB_COL[r], lw=0.9 if r in FIB_RET else 0.7, linestyles="-" if r in FIB_RET else "--")
                etiqueta = (f"ext {1 - r:g}" if r < 0 else f"{r:g}").replace(".", ",")
                ax.text(xf0 - 0.5, v, f"{etiqueta} ({fp(n, v)})", color=FIB_COL[r], fontsize=6.3, ha="right", va="center", clip_on=True)
                ylo, yhi = min(ylo, v - rng * 0.02) if r in FIB_RET else ylo, max(yhi, v + rng * 0.02) if r in FIB_RET else yhi
        ax.plot([X(imp["t0"]), X(imp["t1"])], [imp["p0"], imp["p1"]], color=TV["txt"], lw=0.9, ls=(0, (3, 3)))
    # Bollinger, EMAs
    bb_m = h1.Close.rolling(20).mean(); bb_s = h1.Close.rolling(20).std()
    ax.plot(x, (bb_m + 2 * bb_s).loc[m.index].values, color="#b2b5be", lw=0.6, alpha=0.6)
    ax.plot(x, (bb_m - 2 * bb_s).loc[m.index].values, color="#b2b5be", lw=0.6, alpha=0.6, label="Bollinger 20,2")
    ax.fill_between(x, (bb_m - 2 * bb_s).loc[m.index].values, (bb_m + 2 * bb_s).loc[m.index].values, color="#b2b5be", alpha=0.04)
    ax.plot(x, ema(h1.Close, 20).loc[m.index].values, color=TV["am"], lw=1, label="EMA20")
    ax.plot(x, ema(h1.Close, 50).loc[m.index].values, color=TV["az"], lw=1, label="EMA50")
    # zonas OB/FVG activas de 1h (las 4 más cercanas)
    Z = A["E"]["1h"]["Z"]; q = np.where((Z["creado"] <= AHORA.value) & (Z["fin"] > AHORA.value))[0]
    q = sorted(q, key=lambda z: abs((Z["top"][z] + Z["bot"][z]) / 2 - precio))[:4]
    for z in q:
        c_ = TV["up"] if Z["dir"][z] == 1 else TV["dn"]
        x0 = max(X(Z["creado"][z] - H_NS), 0)
        ax.add_patch(plt.Rectangle((x0, Z["bot"][z]), xr - x0, Z["top"][z] - Z["bot"][z], color=c_, alpha=0.13, lw=0))
        ax.text(x0 + 0.3, Z["top"][z], ("OB" if Z["tipo"][z] == 0 else "FVG") + " 1h", color=c_, fontsize=6, va="bottom")
    # pivotes diarios
    for k, v in pivotes(A, DIA_OP).items():
        if ylo < v < yhi:
            ax.hlines(v, N - 24, xr, colors="#e0e3eb", lw=0.6, linestyles=":", alpha=0.7)
            ax.text(xr, v, f" {k} {fp(n, v)}", color="#e0e3eb", fontsize=6.3, va="center", alpha=0.85, clip_on=True)
    # líneas de tendencia
    for tl in lineas_tendencia(A, AHORA):
        xs = np.array([X(tl["t0"]), N - 1 + 8]); tvs = np.array([tl["t0"], iv[-1] + 8 * H_NS])
        c_ = TV["up"] if tl["tipo"] == "soporte" else TV["dn"]
        ax.plot(xs, [tl["valor"](tv) for tv in tvs], color=c_, lw=1.1)
        ax.text(xs[-1], tl["valor"](tvs[-1]), f" línea de {tl['tipo']}", color=c_, fontsize=6.3, va="center", clip_on=True)
    # velas
    col = np.where(m.Close >= m.Open, TV["up"], TV["dn"])
    ax.vlines(x, m.Low, m.High, color=col, lw=0.8, zorder=4)
    ax.bar(x, np.maximum((m.Close - m.Open).abs(), rng * 0.002), bottom=np.minimum(m.Open, m.Close), width=0.62, color=col, zorder=5)
    # confluencias (marcas a la derecha)
    for c in CF["arriba"] + CF["abajo"]:
        if ylo < c["precio"] < yhi:
            ax.add_patch(plt.Rectangle((N + 0.5, c["precio"] - CF["a1"] * 0.17), 3, CF["a1"] * 0.34, color="#ffeb3b",
                                       alpha=0.25 + 0.12 * min(c["n"], 5), lw=0, zorder=6))
            ax.text(N + 3.8, c["precio"], f"×{c['n']}", color="#ffeb3b", fontsize=6.8, va="center", weight="bold", zorder=7)
    # orden
    if o and o.get("Entrada") is not None:
        x0, x1 = N + 6, N + 20
        for a_, b_, c_ in ((o["Entrada"], o["TP"], TV["up"]), (o["Entrada"], o["SL"], TV["dn"])):
            ax.add_patch(plt.Rectangle((x0, min(a_, b_)), x1 - x0, abs(b_ - a_), color=c_, alpha=0.28, zorder=6))
        for v, t_, c_ in ((o["TP"], "TP", TV["up"]), (o["Entrada"], o.get("Orden", "Entrada"), "white"), (o["SL"], "SL", TV["dn"])):
            ax.hlines(v, x0, x1, colors=c_, lw=1.1, zorder=7)
            ax.text(x0 + 0.3, v, f"{t_} {fp(n, v)}", color=c_, fontsize=7, va="bottom", zorder=8, weight="bold")
    ax.axhline(precio, color="white", lw=0.5, ls=":")
    ax.text(1.0, precio, f" {fp(n, precio)} ", transform=ax.get_yaxis_transform(), color="black", fontsize=7, va="center",
            bbox=dict(fc="white", ec="none", pad=1.2), clip_on=False)
    ax.set_xlim(-1, xr + 7); ax.set_ylim(ylo, yhi); ax.yaxis.tick_right(); ax.set_xticks([])
    ax.text(0.5, 0.5, f"{n} · 1h", transform=ax.transAxes, fontsize=44, color=TV["txt"], alpha=0.05, ha="center", va="center", weight="bold")
    st_txt = f" · {o['Modo']} · {o['Estado']}" if o else ""
    ax.set_title(f"{SIMB.get(n, n)} · 1h · sesgo {sesgo(A, AHORA)[0]:+d}{st_txt}", loc="left", color=TV["txt"], fontsize=10, weight="bold")
    ax.legend(loc="lower left", facecolor=TV["bg"], edgecolor=TV["grid"], labelcolor=TV["txt"], fontsize=6.5)
    # RSI con divergencia
    r = pd.Series(rsi(h1.Close).values, index=h1.index).loc[m.index]
    ar.plot(x, r.values, color="#7e57c2", lw=1); ar.axhline(70, color=TV["sub"], lw=0.6, ls="--"); ar.axhline(30, color=TV["sub"], lw=0.6, ls="--")
    ar.fill_between(x, 30, 70, color="#7e57c2", alpha=0.05); ar.set_ylim(10, 90); ar.set_xlim(-1, xr + 7); ar.yaxis.tick_right()
    dv = divergencia_rsi(A, AHORA)
    if dv:
        nombre, (ta, pa, ra), (tb, pb, rb) = dv; c_ = TV["up"] if nombre == "alcista" else TV["dn"]
        ax.plot([X(ta), X(tb)], [pa, pb], color=c_, lw=1.4); ar.plot([X(ta), X(tb)], [ra, rb], color=c_, lw=1.4)
        ar.text(X(tb), rb, f" divergencia {nombre}", color=c_, fontsize=6.5, va="center")
    ar.text(0.005, 0.85, f"RSI 14 · {r.iloc[-1]:.0f}", transform=ar.transAxes, color="#7e57c2", fontsize=7)
    paso = max(1, N // 10); ar.set_xticks(range(0, N, paso))
    ar.set_xticklabels([f"{DIAS_ES[t_.weekday()]} {t_:%d %H:%M}" for t_ in m.index[::paso]], color=TV["sub"], fontsize=6.6)
    fig.subplots_adjust(left=0.03, right=0.93, top=0.95, bottom=0.06)
    ruta = f"{CARPETA}/analisis_{n}.png"; fig.savefig(ruta, facecolor=TV["bg"]); plt.close(fig)
    return ruta, CF, dv

def lectura(n, CF, dv):
    """Lectura técnica en texto, como la haría un trader."""
    A = ACT[n]; precio = CF["precio"]; pts, raz = sesgo(A, AHORA); L = []
    tr1, tr4 = en_t(A["E"]["1h"]["cierre"], A["E"]["1h"]["tr"], AHORA), en_t(A["E"]["4h"]["cierre"], A["E"]["4h"]["tr"], AHORA)
    tx = lambda v: "alcista" if v > 0 else "bajista" if v < 0 else "neutral"
    L.append(f"Sesgo {pts:+d} ({'comprador' if pts >= UMBRAL_SESGO else 'vendedor' if pts <= -UMBRAL_SESGO else 'sin dirección clara'}); "
             f"estructura 4h {tx(tr4)}, 1h {tx(tr1)}.")
    imp = CF["imp"]
    if imp:
        ret = abs(imp["p1"] - precio) / imp["rango"]
        L.append(f"Fibonacci del último impulso {'alcista' if imp['d'] == 1 else 'bajista'} ({fp(n, imp['p0'])} → {fp(n, imp['p1'])}): "
                 f"el precio retrocede {ret:.0%}. Golden pocket 0,5–0,618 en {fp(n, nivel_fibo(imp, 0.5))}–{fp(n, nivel_fibo(imp, 0.618))}; "
                 f"objetivos de extensión 1,272 en {fp(n, nivel_fibo(imp, -0.272))} y 1,618 en {fp(n, nivel_fibo(imp, -0.618))}.")
    pv = pivotes(A, DIA_OP)
    if pv:
        orden = sorted(pv.items(), key=lambda kv: kv[1]); deb = [k for k, v in orden if v < precio]; enc = [k for k, v in orden if v > precio]
        donde = (f"entre {deb[-1]} y {enc[0]}" if deb and enc else "por encima de R2" if deb else "por debajo de S2")
        L.append(f"Pivotes diarios: precio {donde} (P {fp(n, pv['P'])}, R1 {fp(n, pv['R1'])}, S1 {fp(n, pv['S1'])}).")
    h1 = A["df"].resample("1h").last().Close.dropna(); h1 = h1[h1.index + H1 <= AHORA]
    m20, s20 = h1.rolling(20).mean().iloc[-1], h1.rolling(20).std().iloc[-1]
    anchos = (4 * h1.rolling(20).std() / h1.rolling(20).mean()).dropna()
    pos_bb = (precio - (m20 - 2 * s20)) / (4 * s20) if s20 else 0.5
    L.append(f"Bollinger 1h: precio en el {pos_bb:.0%} de la banda" +
             ("; bandas comprimidas (posible ruptura cercana)." if len(anchos) > 100 and anchos.iloc[-1] < anchos.iloc[-100:].quantile(0.2) else "."))
    r1 = en_t(A["h1c"], A["rsi"], AHORA)
    L.append(f"RSI 1h {r1:.0f}" + (f"; divergencia {dv[0]} entre los dos últimos swings." if dv else "; sin divergencias.")
             + (" Sobrecompra." if r1 > 70 else " Sobreventa." if r1 < 30 else ""))
    for tl in lineas_tendencia(A, AHORA):
        v = tl["valor"](AHORA.value)
        L.append(f"Línea de {tl['tipo']} 1h en {fp(n, v)} ({'el precio la respeta' if (precio - v) * (1 if tl['tipo'] == 'soporte' else -1) > 0 else 'el precio la rompió'}).")
    for lado, nombre in (("arriba", "Resistencias"), ("abajo", "Soportes")):
        if CF[lado]:
            L.append(f"{nombre} con confluencia: " + "; ".join(f"{fp(n, c['precio'])} ×{c['n']} ({', '.join(c['herramientas'])})"
                                                           for c in CF[lado]) + ".")
    return L

G, ANALISIS = {}, {}
for n in ACT:
    o = next((o for o in ORDENES if o["Activo"] == n and o["Estado"] in ("ENTRAR AHORA", "EN CURSO", "PENDIENTE", "PRELIMINAR")), None)
    ruta, CF, dv = grafico_analisis(n, o)
    if o and o.get("imp"): CF["imp"] = o["imp"]
    ANALISIS[n] = dict(ruta=ruta, texto=lectura(n, CF, dv), orden=o)
if len(CARTERA):
    fig, ax = _fig((12, 4.2)); c = CARTERA.sort_values("t_entrada")
    ax.plot(c.t_entrada.dt.tz_localize(None), c.R.cumsum(), color=TV["az"], lw=1.4)
    ax.axvspan(F_CORTE, pd.Timestamp(DIAS[-1]) + pd.Timedelta(days=1), color=TV["sub"], alpha=0.15)
    ax.text(F_CORTE, ax.get_ylim()[1], "  FUERA DE MUESTRA", color=TV["txt"], va="top")
    ax.axhline(0, color=TV["sub"], lw=0.7, ls=":")
    ax.set_title("Cartera aprobada: resultado acumulado (R, después de costos)", loc="left", color=TV["txt"], fontsize=10, weight="bold")
    G["equity"] = _save(fig, "equity_cartera")
if len(SIMS):
    fig, ax = _fig((12, 3.2)); real = C_OOS.R.sum()
    ax.hist(SIMS, bins=50, color=TV["sub"], alpha=0.8, label="dirección al azar")
    ax.axvline(real, color=TV["am"], lw=2, label=f"cartera real {real:+.1f}R (p = {P_VAL:.3f})")
    ax.legend(facecolor=TV["bg"], edgecolor=TV["grid"], labelcolor=TV["txt"])
    ax.set_title("Test de permutación fuera de muestra", loc="left", color=TV["txt"], fontsize=10, weight="bold")
    G["perm"] = _save(fig, "permutacion")


# ===================================== SALIDA EN COLAB =====================================
def txt_m(m):
    if m.get("n", 0) == 0: return "sin operaciones"
    return (f"{m['n']} ops · acierto {m['win']:.0f}% · expectativa {m['exp']:+.3f}R · PF {m['pf']:.2f} · "
            f"DD máx {m['dd_pct']:.1f}% · racha perdedora {m['racha']} · {m['por_mes']:.1f} ops/mes")
ENC = (f"Órdenes para {DIAS_ES[pd.Timestamp(DIA_OP).weekday()]} {pd.Timestamp(DIA_OP):%d-%m-%Y} · datos hasta {fmt_hora(AHORA)} · "
       f"ventana {T0_HOY:%H:%M}–{T1_HOY:%H:%M} · cuenta {CUENTA_USD:,.2f} {ACC.currency if ACC else 'USD'} · riesgo {RIESGO_PCT:g}% por operación")
display(Markdown(f"# {ETIQUETA}\n**Veredicto de la validación: {VER[0]}.** {VER[1]}\n\n{ENC}" +
                 (f"\n\n> ⚠️ {AVISO_DATOS}" if AVISO_DATOS else "")))
display(pd.DataFrame(filas_ordenes(), columns=CAB).set_index("Activo") if ORDENES else
        Markdown("Ningún activo/modo aprobado: no hay órdenes."))
display(Markdown("\n".join(f"- **{o['Activo']} · {o['Modo']} · {o['Estado']}:** {o.get('Nota', '')}" for o in ORDENES) +
                 ("\n\n> Precios directos de tu cuenta MT5: los niveles coinciden con tu plataforma." if MT5 is not None else
                  "\n\n> Datos Dukascopy (oro spot), con algunos minutos de retraso. Si tu bróker cotiza distinto, ajusta los niveles.")))
for n, a in ANALISIS.items():
    display(Markdown(f"### {n}\n" + "\n".join(f"- {x}" for x in a["texto"])))
    display(Image(a["ruta"]))
display(Markdown(f"## Validación ({DIAS[0]} → {DIAS[-1]})\n"
                 f"- In-sample ({DIAS_IS[0]} → {DIAS_IS[-1]}): {txt_m(M_IS)}\n"
                 f"- **Fuera de muestra** ({DIAS_OOS[0]} → {DIAS_OOS[-1]}): {txt_m(M_OOS)}\n"
                 f"- p-valor de permutación: {P_VAL:.3f} · IC 95% de la expectativa: [{IC[0]:+.3f}, {IC[1]:+.3f}]"))
display(TABLA_APROB.style.format({"Exp. IS": "{:+.3f}", "Exp. OOS": "{:+.3f}", "PF IS": "{:.2f}"}, na_rep="—"))
for k in ("equity", "perm"):
    if k in G: display(Image(G[k]))


# ===================================== INFORME PDF =====================================
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
_fd = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont("DV", os.path.join(_fd, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DVB", os.path.join(_fd, "DejaVuSans-Bold.ttf")))
AZUL, GRIS, LINEA = colors.HexColor("#1e3a5f"), colors.HexColor("#5f6b7a"), colors.HexColor("#d5dae1")
VERDE, ROJO, AMBAR = colors.HexColor("#1b8a5a"), colors.HexColor("#c0392b"), colors.HexColor("#b7791f")
st = {"h1": ParagraphStyle("h1", fontName="DVB", fontSize=19, leading=23, spaceAfter=3),
      "h2": ParagraphStyle("h2", fontName="DVB", fontSize=12.5, leading=15, textColor=AZUL, spaceBefore=6, spaceAfter=3),
      "p": ParagraphStyle("p", fontName="DV", fontSize=8.8, leading=12),
      "li": ParagraphStyle("li", fontName="DV", fontSize=8.4, leading=11, leftIndent=10, bulletIndent=2),
      "sub": ParagraphStyle("sub", fontName="DV", fontSize=8.8, leading=12, textColor=GRIS),
      "td": ParagraphStyle("td", fontName="DV", fontSize=7.5, leading=9.2),
      "th": ParagraphStyle("th", fontName="DVB", fontSize=7.5, leading=9.2, textColor=colors.white)}
esc = lambda s: html.escape(str(s)); P = lambda s, k="p": Paragraph(esc(s), st[k])
bullets = lambda L: [Paragraph(esc(x), st["li"], bulletText="•") for x in L]
ANCHO = landscape(A4)[0] - 3 * cm
def tabla(filas, cab, colores=None, anchos=None):
    t = Table([[Paragraph(esc(c), st["th"]) for c in cab]] + [[Paragraph(esc(v), st["td"]) for v in f] for f in filas],
              colWidths=anchos, repeatRows=1)
    sty = [("BACKGROUND", (0, 0), (-1, 0), AZUL), ("GRID", (0, 0), (-1, -1), 0.4, LINEA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f9")])]
    for (c, r), col in (colores or {}).items(): sty.append(("TEXTCOLOR", (c, r), (c, r), col))
    t.setStyle(TableStyle(sty)); return t
def img(r, alto):
    w, h = ImageReader(r).getSize(); a = min(ANCHO, alto * w / h); return RLImage(r, width=a, height=a * h / w)
def pie(c, doc):
    c.saveState(); W, H = landscape(A4); c.setStrokeColor(LINEA); c.line(1.5*cm, H - 1.1*cm, W - 1.5*cm, H - 1.1*cm)
    c.setFont("DV", 7.5); c.setFillColor(GRIS)
    c.drawString(1.5*cm, H - 0.9*cm, f"Sistema de trading · {ENC}")
    c.drawRightString(W - 1.5*cm, H - 0.9*cm, f"Página {doc.page}")
    c.drawString(1.5*cm, 0.8*cm, "Resultados pasados no garantizan resultados futuros. No constituye recomendación de inversión.")
    c.restoreState()

PDF = f"{CARPETA}/Sistema_trading_{pd.Timestamp(DIA_OP):%Y-%m-%d}_{AHORA:%H%M}.pdf"
doc = SimpleDocTemplate(PDF, pagesize=landscape(A4), leftMargin=1.5*cm, rightMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.3*cm,
                        title="Sistema de trading — órdenes y validación")
col_v = VERDE if APTA else (AMBAR if VER[0] == "POSITIVO PERO NO CONCLUYENTE" else ROJO)
colr = {(3, i): {"ENTRAR AHORA": VERDE, "EN CURSO": VERDE, "PENDIENTE": AZUL, "PRELIMINAR": AZUL, "OMITIDA": ROJO}.get(o["Estado"], GRIS)
        for i, o in enumerate(ORDENES, start=1)}
S = [Paragraph(esc(ETIQUETA), ParagraphStyle("e", parent=st["h1"], textColor=col_v)),
     P(f"Veredicto de la validación: {VER[0]}. {VER[1]}"), P(ENC, "sub")] + ([P(AVISO_DATOS, "sub")] if AVISO_DATOS else []) + [Spacer(1, 6)]
S += [tabla(filas_ordenes(), CAB, colr) if ORDENES else P("Ningún activo/modo aprobado: no hay órdenes."), Spacer(1, 5)]
S += bullets([f"{o['Activo']} · {o['Modo']} · {o['Estado']}: {o.get('Nota', '')}" for o in ORDENES])
S += [Spacer(1, 4), P("Reglas de uso: poner SL y TP en el bróker al abrir; no mover el SL en contra; parar el día tras 2 pérdidas; "
                      "re-ejecutar el sistema para actualizar estados (cada 30 min en la ventana). Lotes para contratos estándar "
                      "(forex 100.000, oro 100 oz, BTC 1): verificar con tu bróker.", "sub"), PageBreak()]
for n, a in ANALISIS.items():
    S += [P(f"Análisis técnico · {n}" + (f" ({SIMB[n]})" if SIMB.get(n, n) != n else ""), "h2"), img(a["ruta"], 11.6*cm)]
    S += bullets(a["texto"]) + [PageBreak()]
S += [P("Validación de la estrategia", "h2"),
      P(f"Periodo {DIAS[0]} → {DIAS[-1]} ({len(DIAS)} días) · datos {FUENTE} · costos: spread + {SLIPPAGE_PIPS} pips de slippage por lado. "
        f"Cada activo y modo se aprueba solo con datos in-sample (≥ {MIN_IS} operaciones, expectativa > {EXP_MIN_IS}R, PF > 1,1); "
        f"la cartera aprobada se juzga en el periodo fuera de muestra, que no participó en la elección.")]
S += bullets([f"In-sample: {txt_m(M_IS)}", f"Fuera de muestra: {txt_m(M_OOS)}",
              f"p-valor de permutación {P_VAL:.3f} · IC 95% de la expectativa [{IC[0]:+.3f}, {IC[1]:+.3f}]"])
if "equity" in G: S.append(img(G["equity"], 6.6*cm))
if "perm" in G: S.append(img(G["perm"], 4.6*cm))
S += [PageBreak(), P("Aprobación por activo y modo", "h2"),
      tabla([[r.Activo, r.Modo, f"{r['Ops IS']}", "—" if np.isnan(r["Exp. IS"]) else f"{r['Exp. IS']:+.3f}",
              "—" if np.isnan(r["PF IS"]) else f"{r['PF IS']:.2f}", f"{r['Ops OOS']}",
              "—" if np.isnan(r["Exp. OOS"]) else f"{r['Exp. OOS']:+.3f}", r.Aprobado] for _, r in TABLA_APROB.iterrows()],
            list(TABLA_APROB.columns),
            {(7, i + 1): VERDE for i, a in enumerate(TABLA_APROB.Aprobado) if a == "SÍ"})]
S += [Spacer(1, 6), P("Cómo funciona", "h2")] + bullets([
    "Sesgo de −7 a +7 a la apertura de Londres: cambio y rango semanal, EMAs 1h, estructura 1h y 4h, cierre diario vs EMA20, RSI 1h.",
    "Modo confirmación: entra a mercado cuando una vela de 30m a favor reacciona en un OB/FVG, barre liquidez (Asia, día o semana "
    "anterior) o rebota en la EMA20 de 1h, entre la apertura de Londres y el mediodía de NY, evitando datos de EE.UU. y la apertura de Wall Street.",
    "Modo límite: a la apertura de Londres coloca una orden limitada en la zona más cercana a favor del sesgo; se cancela si no se llena "
    "antes del mediodía de NY o si el precio llega al objetivo sin llenarla.",
    "Modo fibo: a la apertura de Londres toma el último impulso de 1h a favor del sesgo y coloca una orden limitada en su 0,618; "
    "SL detrás del origen del impulso (1,0) y TP en el extremo (0) o en la extensión 1,272 si el RR es menor a 1,5.",
    "Gráficos: Fibonacci con extensiones, pivotes diarios, líneas de tendencia por swings, Bollinger 20,2, EMAs, OB/FVG, RSI con "
    "divergencias y zonas de confluencia (×N = cantidad de herramientas que coinciden en ese precio).",
    "SL detrás de la zona; TP en la liquidez más cercana con al menos 1,5R. Todas las operaciones se cierran al final del día.",
    "Máximo una operación por activo y dos por lado del dólar al día. Si SL y TP se tocan en la misma vela, se asume el SL.",
    "Elegir activos por sus resultados pasados puede sobreajustar: por eso la decisión final la da el periodo fuera de muestra."])
doc.build(S, onFirstPage=pie, onLaterPages=pie)
CARTERA.to_csv(f"{CARPETA}/operaciones_cartera.csv", index=False)
print(f"\n📄 Informe: {PDF}")
try:
    from google.colab import files
    files.download(PDF)
except Exception:
    if sys.platform.startswith("win"):
        try: os.startfile(os.path.abspath(PDF))            # abre el PDF en tu PC
        except Exception: pass
if MT5 is not None:
    MT5.shutdown()
