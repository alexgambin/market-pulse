"""
precios.py — Capa B: los ojos al precio.

Fuente: Yahoo Finance vía yfinance. Peticiones secuenciales y espaciadas +
caché de 10 min: cadencia educada.

Autotest:  python precios.py
"""
import asyncio
import time

_PARES_FX = {"EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF", "USD/CAD"}

INSTRUMENTOS = {
    "WTI": "CL=F",
    "Brent": "BZ=F",
    "USD/JPY": "JPY=X",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CHF": "CHF=X",
    "USD/CAD": "CAD=X",
    "S&P 500": "^GSPC",
    "Oro": "GC=F",
    "Gas nat.": "NG=F",
    "DXY": "DX-Y.NYB",
    "VIX": "^VIX",
    "US 10A": "^TNX",
    "Nasdaq": "^IXIC",
    "Russell 2000": "^RUT",
    "Dow (US30)": "^DJI",
    "Plata": "SI=F",
    "Cobre": "HG=F",
    "Platino": "PL=F",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}

# Correcciones de escala por instrumento (vacío: yfinance ya entrega ^TNX en %,
# al contrario que la web de Yahoo — comprobado empíricamente el 23/7)
_DIVISORES: dict[str, float] = {}

_TTL_SEGUNDOS = 600
_PAUSA_ENTRE_PETICIONES = 0.4
_cache: dict = {"ts": 0.0, "texto": None}
ultimo_diagnostico: dict = {}


def _obtener_uno(simbolo: str, simbolo_nombre: str = "") -> dict | None:
    import yfinance as yf

    t = yf.Ticker(simbolo)
    h = t.history(period="10d", interval="1d")
    cierres = [c for c in h["Close"].tolist() if c == c] if not h.empty else []
    if len(cierres) < 2:
        return None

    ultimo, previo = cierres[-1], cierres[-2]
    en_vivo = False
    try:
        fi = t.fast_info
        pv = fi.get("last_price") or fi.get("lastPrice")
        pc = fi.get("previous_close") or fi.get("previousClose")
        if pv and pc:
            ultimo, previo, en_vivo = float(pv), float(pc), True
    except Exception:
        pass

    var_1d = (ultimo / previo - 1) * 100
    var_5d = (ultimo / cierres[-6] - 1) * 100 if len(cierres) >= 6 else None
    return {"ultimo": ultimo / _DIVISORES.get(simbolo_nombre, 1.0),
            "var_1d": var_1d, "var_5d": var_5d,
            "hora": "en vivo" if en_vivo else "últ. cierre"}


def _obtener_todos() -> list[tuple[str, dict | None]]:
    resultados = []
    for nombre, simbolo in INSTRUMENTOS.items():
        try:
            datos = _obtener_uno(simbolo, nombre)
            if datos is None:
                ultimo_diagnostico[nombre] = "FALLO — sin datos (¿429 reciente? espera unos min)"
            else:
                ultimo_diagnostico[nombre] = f"OK — {datos['ultimo']:.2f} ({datos['hora']})"
            resultados.append((nombre, datos))
        except Exception as e:
            ultimo_diagnostico[nombre] = f"FALLO — {type(e).__name__}: {str(e)[:60]}"
            resultados.append((nombre, None))
        time.sleep(_PAUSA_ENTRE_PETICIONES)
    return resultados


async def instantanea(nombres: list[str]) -> dict:
    """Precios frescos (sin caché) de un subconjunto — para el vigilante de rupturas."""
    def _lote():
        out = {}
        for nombre in nombres:
            simbolo = INSTRUMENTOS.get(nombre)
            if not simbolo:
                continue
            try:
                d = _obtener_uno(simbolo, nombre)
                if d:
                    out[nombre] = d
            except Exception:
                pass
            time.sleep(_PAUSA_ENTRE_PETICIONES)
        return out
    return await asyncio.to_thread(_lote)


async def cinta() -> str:
    """Bloque de texto con los precios reales, listo para inyectar en un prompt."""
    if _cache["texto"] is not None and time.time() - _cache["ts"] < _TTL_SEGUNDOS:
        return _cache["texto"]

    resultados = await asyncio.to_thread(_obtener_todos)

    lineas, fallos = [], []
    for nombre, d in resultados:
        if d is None:
            fallos.append(nombre)
            continue
        v5 = f"{d['var_5d']:+.1f}% 5d" if d["var_5d"] is not None else ""
        dec = 4 if nombre in _PARES_FX else 2
        lineas.append(
            f"{nombre:<8} {d['ultimo']:>10.{dec}f}  {d['var_1d']:+.2f}% d  {v5:<10} ({d['hora']})"
        )

    if not lineas:
        texto = (
            "== CINTA (precios reales) ==\nNo disponible en esta pasada: no inventes "
            "niveles; trata los precios de artículos como no verificados."
        )
    else:
        aviso = f"\nSin datos ahora: {', '.join(fallos)}." if fallos else ""
        texto = (
            "== CINTA (precios reales de mercado, Yahoo/yfinance, retardo de minutos; "
            "ÚNICA fuente válida de niveles actuales) ==\n" + "\n".join(lineas) + aviso
        )

    _cache.update(ts=time.time(), texto=texto)
    return texto


if __name__ == "__main__":
    print(asyncio.run(cinta()))
    print("\nDiagnóstico por instrumento:")
    ancho = max(len(n) for n in INSTRUMENTOS)
    for nombre in INSTRUMENTOS:
        print(f"  {nombre:<{ancho}}  {ultimo_diagnostico.get(nombre, '—')}")


def historial_validacion(instrumento, inicio, fin):
    """OHLC diario crudo, rango inclusivo; errores se propagan al Validation Engine.

    Las cotizaciones ajustadas retroactivamente no sirven para niveles absolutos.
    No usa fast_info ni la cinta actual como sustituto del histórico.
    Inicio/fin son límites conservadores de sesión, no horas de liquidación.
    """
    from datetime import datetime, time, timedelta
    import yfinance as yf

    simbolo = INSTRUMENTOS[instrumento]
    h = yf.Ticker(simbolo).history(start=inicio.isoformat(),
                                  end=(fin + timedelta(days=1)).isoformat(),
                                  interval="1d", auto_adjust=False, back_adjust=False,
                                  raise_errors=True, timeout=20)
    if h.empty:
        return []
    if h.index.tz is None:
        raise ValueError("El proveedor no identifica la zona de las sesiones")
    barras = []
    # Futuros/FX pueden empezar la tarde anterior a la etiqueta de sesión.
    anterior = simbolo.endswith("=F") or simbolo.endswith("=X") or instrumento == "DXY"
    for ts, row in h.iterrows():
        dia = ts.date()
        zona = h.index.tz
        # pandas localiza correctamente pytz/zoneinfo, también durante DST.
        import pandas as pd
        comienzo = pd.Timestamp(datetime.combine(dia - timedelta(days=int(anterior)), time.min)).tz_localize(zona)
        final = pd.Timestamp(datetime.combine(dia + timedelta(days=1), time.min)).tz_localize(zona)
        divisor = _DIVISORES.get(instrumento, 1.0)
        barras.append(dict(fecha=dia.isoformat(), inicio=comienzo.isoformat(), fin=final.isoformat(),
                           high=float(row['High']) / divisor, low=float(row['Low']) / divisor,
                           close=float(row['Close']) / divisor))
    return barras
