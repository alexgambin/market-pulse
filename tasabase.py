"""
tasabase.py — Tasas base para anclar las predicciones (Punto 1).

Responde "¿cuántas veces ha pasado esto de verdad?" con el histórico diario de
yfinance. El agente debe consultar la clase de referencia ANTES de poner una
probabilidad, para no dejarse arrastrar por la saliencia de la noticia.

Ejemplos que resuelve:
  - ¿cuántas veces en 3 años el WTI se movió >4% en 5 días?
  - ¿con qué frecuencia el USD/JPY cerró la semana por encima de donde la abrió?

Autotest:  python tasabase.py
"""
import asyncio

import precios  # reutiliza el mapa de símbolos y yfinance

# ventanas de referencia típicas → nº de sesiones (~21 sesiones/mes bursátil)
VENTANAS = {
    "1d": 1, "3d": 3, "5d": 5, "10d": 10, "20d": 20,
    "30d": 21, "60d": 42, "90d": 63, "180d": 126, "365d": 252,
}


def _sesiones_desde_dias(dias_naturales: int) -> int:
    """Convierte días naturales de calendario a sesiones bursátiles aproximadas.

    Cripto cotiza 7 días pero yfinance da velas diarias; para materias/FX/índices
    hay ~21 sesiones cada 30 días naturales. Usamos ese factor como aproximación
    común (para el puente, que necesita el horizonte exacto del mercado)."""
    return max(1, round(dias_naturales * 21 / 30))


def _serie_cierres(simbolo: str, dias: int) -> list[float]:
    import yfinance as yf

    h = yf.Ticker(simbolo).history(period=f"{dias}d", interval="1d")
    return [c for c in h["Close"].tolist() if c == c]


def _tasa(instrumento: str, direccion: str, umbral_pct: float,
          ventana: str, anios: int, pasos_exactos: int = None) -> dict | None:
    simbolo = precios.INSTRUMENTOS.get(instrumento)
    if not simbolo:
        return {"error": f"instrumento desconocido: {instrumento}"}
    # pasos_exactos tiene prioridad (modo puente); si no, se usa la ventana con nombre
    if pasos_exactos is not None:
        pasos = pasos_exactos
        etiqueta_ventana = f"{pasos}sesiones"
    else:
        pasos = VENTANAS.get(ventana)
        etiqueta_ventana = ventana
        if not pasos:
            return {"error": f"ventana no válida: {ventana} (usa {list(VENTANAS)})"}

    # para ventanas largas hace falta más histórico para tener muestras suficientes
    anios_efectivos = max(anios, (pasos // 200) + anios)
    cierres = _serie_cierres(simbolo, anios_efectivos * 365 + pasos + 40)
    if len(cierres) < pasos + 30:
        return {"error": "histórico insuficiente"}

    movimientos = [
        (cierres[i + pasos] / cierres[i] - 1) * 100
        for i in range(len(cierres) - pasos)
    ]
    u = abs(umbral_pct)
    if direccion == "sube":
        cumplen = sum(1 for m in movimientos if m >= u)
    elif direccion == "baja":
        cumplen = sum(1 for m in movimientos if m <= -u)
    else:  # "mueve": en cualquier dirección
        cumplen = sum(1 for m in movimientos if abs(m) >= u)

    n = len(movimientos)
    frecuencia = cumplen / n if n else 0.0
    return {
        "instrumento": instrumento,
        "clase": f"{instrumento} {direccion} {u:.1f}% en {etiqueta_ventana}",
        "frecuencia": round(frecuencia, 3),
        "ocurrencias": cumplen,
        "muestras": n,
        "anios": anios_efectivos,
    }


async def tasa_base(instrumento: str, direccion: str = "mueve",
                    umbral_pct: float = 2.0, ventana: str = "5d",
                    anios: int = 3, pasos_exactos: int = None) -> dict:
    """Herramienta del agente: frecuencia histórica de un tipo de movimiento."""
    try:
        return await asyncio.to_thread(
            _tasa, instrumento, direccion, umbral_pct, ventana, anios, pasos_exactos
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:80]}"}


async def tasa_base_dias(instrumento: str, direccion: str, umbral_pct: float,
                         dias_naturales: int, anios: int = 5) -> dict:
    """Como tasa_base pero para el horizonte EXACTO de un mercado (en días naturales).

    Convierte los días a sesiones bursátiles y calcula la frecuencia justa para ese
    horizonte — lo que necesita el puente para no comparar 135 días con 20. Pide 5
    años de histórico por defecto para tener muestras suficientes en ventanas largas.
    """
    pasos = _sesiones_desde_dias(dias_naturales)
    try:
        r = await asyncio.to_thread(
            _tasa, instrumento, direccion, umbral_pct, "exacta", anios, pasos
        )
        if r and "error" not in r:
            r["dias_naturales"] = dias_naturales
            r["clase"] = f"{instrumento} {direccion} {abs(umbral_pct):.1f}% en {dias_naturales}d"
        return r
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:80]}"}


if __name__ == "__main__":
    casos = [
        ("WTI", "mueve", 4.0, "5d", 3),
        ("WTI", "sube", 4.0, "5d", 3),
        ("USD/JPY", "mueve", 2.0, "5d", 3),
        ("Oro", "sube", 3.0, "10d", 3),
        ("EUR/USD", "baja", 1.5, "5d", 3),
    ]
    for inst, dirn, u, v, a in casos:
        r = asyncio.run(tasa_base(inst, dirn, u, v, a))
        if "error" in r:
            print(f"  {inst:<8} {dirn} {u}% {v}: FALLO — {r['error']}")
        else:
            print(f"  {r['clase']:<32} → {r['frecuencia']:.1%} "
                  f"({r['ocurrencias']}/{r['muestras']} en {a}a)")
