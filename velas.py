"""
velas.py — Histórico OHLC para graficar predicciones y mercados (yfinance).
Formato lightweight-charts: [{time, open, high, low, close}].
Soporta varias temporalidades. Nota de yfinance: los datos intradía (1h, 4h)
solo existen para los últimos ~60 días; diario/semanal/mensual llegan años atrás.
"""
import asyncio

import precios  # mapa instrumento → símbolo

# temporalidad → (intervalo yfinance, período por defecto, etiqueta)
# 4h no existe nativo en yfinance: se remuestrea desde 1h.
TEMPORALIDADES = {
    "1D": {"interval": "1d",  "period": "180d", "resample": None},
    "4h": {"interval": "1h",  "period": "60d",  "resample": "4h"},
    "1h": {"interval": "1h",  "period": "30d",  "resample": None},
    "1W": {"interval": "1wk", "period": "3y",   "resample": None},
    "1M": {"interval": "1mo", "period": "10y",  "resample": None},
}


def _ohlc(simbolo: str, temporalidad: str) -> list[dict]:
    import yfinance as yf

    cfg = TEMPORALIDADES.get(temporalidad, TEMPORALIDADES["1D"])
    h = yf.Ticker(simbolo).history(period=cfg["period"], interval=cfg["interval"])
    if h.empty:
        return []

    # remuestreo a 4h desde velas de 1h
    if cfg["resample"]:
        h = h.resample(cfg["resample"], label="left", closed="left").agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last",
        }).dropna()

    intradia = cfg["interval"] in ("1h",) or cfg["resample"]
    velas = []
    for idx, fila in h.iterrows():
        o, hi, lo, cl = fila["Open"], fila["High"], fila["Low"], fila["Close"]
        if any(v != v for v in (o, hi, lo, cl)):  # descarta NaN
            continue
        # lightweight-charts: intradía usa timestamp UNIX; diario+ usa 'YYYY-MM-DD'
        t = int(idx.timestamp()) if intradia else idx.strftime("%Y-%m-%d")
        velas.append({
            "time": t,
            "open": round(float(o), 4), "high": round(float(hi), 4),
            "low": round(float(lo), 4), "close": round(float(cl), 4),
        })
    return velas


async def velas(instrumento: str, temporalidad: str = "1D") -> list[dict]:
    clave = resolver_instrumento(instrumento)
    simbolo = precios.INSTRUMENTOS.get(clave) if clave else None
    if not simbolo:
        return []
    try:
        return await asyncio.to_thread(_ohlc, simbolo, temporalidad)
    except Exception:
        return []


# alias frecuentes que el agente puede escribir de otra forma
_ALIAS = {
    "gold": "Oro", "oro spot": "Oro", "xauusd": "Oro", "xau/usd": "Oro",
    "wti crudo": "WTI", "crudo wti": "WTI", "petróleo": "WTI", "petroleo": "WTI",
    "brent crudo": "Brent", "eurusd": "EUR/USD", "usdjpy": "USD/JPY",
    "gbpusd": "GBP/USD", "audusd": "AUD/USD", "usdchf": "USD/CHF", "usdcad": "USD/CAD",
    "s&p": "S&P 500", "sp500": "S&P 500", "spx": "S&P 500", "yen": "USD/JPY", "euro": "EUR/USD",
    "bitcoin": "BTC", "btc-usd": "BTC", "btcusd": "BTC", "xbt": "BTC",
    "ethereum": "ETH", "eth-usd": "ETH", "ethusd": "ETH", "ether": "ETH",
    "nasdaq": "Nasdaq", "ixic": "Nasdaq",
}


def resolver_instrumento(nombre: str) -> str | None:
    """Casa un nombre libre del agente con una clave de la cinta, tolerando coletillas."""
    if not nombre:
        return None
    if nombre in precios.INSTRUMENTOS:
        return nombre
    limpio = nombre.strip()
    bajo = limpio.lower()
    base = bajo.split("(")[0].strip()
    if base in _ALIAS:
        return _ALIAS[base]
    for clave in precios.INSTRUMENTOS:
        cl = clave.lower()
        if base == cl or bajo == cl or base.startswith(cl) or cl.startswith(base):
            return clave
    return _ALIAS.get(bajo)
