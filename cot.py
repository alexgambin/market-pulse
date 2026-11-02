"""
cot.py — Posicionamiento semanal COT de la CFTC (API Socrata pública, sin clave).

Autotest:  python cot.py
"""
import asyncio
import time

import httpx

_BASE = "https://publicreporting.cftc.gov/resource"

CONTRATOS = {
    "WTI": ("72hh-3qpy", "067651", "m_money", "managed money"),
    "Yen (6J vs USD)": ("gpe5-46if", "097741", "lev_money", "leveraged funds"),
    "Oro": ("72hh-3qpy", "088691", "m_money", "managed money"),
    "Plata": ("72hh-3qpy", "084691", "m_money", "managed money"),
    "Euro (6E vs USD)": ("gpe5-46if", "099741", "lev_money", "leveraged funds"),
    "Bitcoin (CME)": ("gpe5-46if", "133741", "lev_money", "leveraged funds"),
}

_CABECERAS = {"User-Agent": "market-pulse-personal/1.0"}
_TTL_SEGUNDOS = 24 * 3600
_cache: dict = {"ts": 0.0, "texto": None}
_cache_hist: dict = {}
ultimo_diagnostico: dict = {}


def _valor(fila: dict, prefijo: str, lado: str) -> float:
    exacto = f"{prefijo}_positions_{lado}_all"
    if exacto in fila:
        return float(fila[exacto])
    candidatos = [
        k for k in fila
        if k.startswith(f"{prefijo}_positions_{lado}")
        and "_old" not in k and "_other" not in k
    ]
    if not candidatos:
        raise KeyError(f"{prefijo}_positions_{lado}*")
    return float(fila[sorted(candidatos, key=len)[0]])


def _parsear(filas: list[dict], prefijo: str) -> dict | None:
    try:
        def neto(f):
            return int(_valor(f, prefijo, "long")) - int(_valor(f, prefijo, "short"))

        actual = filas[0]
        n_actual = neto(actual)
        delta = n_actual - neto(filas[1]) if len(filas) > 1 else None
        return {
            "neto": n_actual,
            "delta": delta,
            "oi": int(float(next(
                (actual[k] for k in ("open_interest_all", "open_interest") if k in actual), 0))),
            "fecha": actual.get("report_date_as_yyyy_mm_dd", "")[:10],
        }
    except (KeyError, ValueError, IndexError, TypeError):
        return None


async def _obtener(web: httpx.AsyncClient, nombre: str, ds: str, codigo: str, prefijo: str):
    url = (
        f"{_BASE}/{ds}.json?$where=cftc_contract_market_code='{codigo}'"
        f"&$order=report_date_as_yyyy_mm_dd DESC&$limit=2"
    )
    try:
        r = await web.get(url)
        if r.status_code != 200:
            ultimo_diagnostico[nombre] = f"FALLO — HTTP {r.status_code}"
            return nombre, None
        datos = _parsear(r.json(), prefijo)
        if datos is None:
            filas = r.json() if r.text.startswith("[") else []
            campos = ", ".join(sorted(k for k in (filas[0] if filas else {}) if "position" in k))[:90]
            ultimo_diagnostico[nombre] = (
                f"FALLO — campos de posición del dataset: «{campos}»" if campos
                else f"FALLO — respuesta inesperada: «{r.text[:60]}»")
            return nombre, None
        ultimo_diagnostico[nombre] = f"OK — neto {datos['neto']:+,} ({datos['fecha']})"
        return nombre, datos
    except Exception as e:
        ultimo_diagnostico[nombre] = f"FALLO — {type(e).__name__}: {str(e)[:60]}"
        return nombre, None


async def resumen() -> str:
    if _cache["texto"] is not None and time.time() - _cache["ts"] < _TTL_SEGUNDOS:
        return _cache["texto"]

    async with httpx.AsyncClient(timeout=15, headers=_CABECERAS) as web:
        resultados = await asyncio.gather(
            *(_obtener(web, nombre, ds, cod, pref)
              for nombre, (ds, cod, pref, _) in CONTRATOS.items())
        )

    lineas = []
    for nombre, d in resultados:
        if d is None:
            continue
        delta = f"{d['delta']:+,} en la semana" if d["delta"] is not None else "sin dato previo"
        etiqueta = CONTRATOS[nombre][3]
        lineas.append(
            f"{nombre} ({etiqueta}): neto {d['neto']:+,} contratos ({delta}); "
            f"OI {d['oi']:,} (informe del {d['fecha']})"
        )

    if any(n.startswith(("Yen", "Euro")) for n, d in resultados if d is not None):
        lineas.append(
            "(Lectura de divisas: el 6J cotiza yen contra dólar y el 6E euro contra "
            "dólar — neto NEGATIVO = fondos cortos de esa divisa. Corto de yen = a favor "
            "de USD/JPY al alza; corto de euro = a favor de EUR/USD a la baja. Un unwind "
            "les obliga a recomprarla y el par gira.)"
        )

    if not lineas:
        texto = ""
    else:
        texto = (
            "== POSICIONAMIENTO COT (CFTC, semanal — foto del martes publicada el "
            "viernes) ==\n" + "\n".join(lineas)
        )

    _cache.update(ts=time.time(), texto=texto)
    return texto


def _serie(filas: list[dict], prefijo: str) -> list[dict]:
    puntos = []
    for f in filas:
        try:
            puntos.append({
                "fecha": f.get("report_date_as_yyyy_mm_dd", "")[:10],
                "neto": int(_valor(f, prefijo, "long")) - int(_valor(f, prefijo, "short")),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return list(reversed(puntos))


async def historico(nombre: str, semanas: int = 52) -> list[dict]:
    clave = (nombre, semanas)
    en_cache = _cache_hist.get(clave)
    if en_cache and time.time() - en_cache["ts"] < _TTL_SEGUNDOS:
        return en_cache["serie"]

    ds, codigo, prefijo, _ = CONTRATOS[nombre]
    url = (
        f"{_BASE}/{ds}.json?$where=cftc_contract_market_code='{codigo}'"
        f"&$order=report_date_as_yyyy_mm_dd DESC&$limit={int(semanas)}"
    )
    try:
        async with httpx.AsyncClient(timeout=20, headers=_CABECERAS) as web:
            r = await web.get(url)
            r.raise_for_status()
            serie = _serie(r.json(), prefijo)
    except Exception:
        serie = []
    _cache_hist[clave] = {"ts": time.time(), "serie": serie}
    return serie


if __name__ == "__main__":
    salida = asyncio.run(resumen())
    print(salida or "(sin datos)")
    print("\nDiagnóstico:")
    for nombre in CONTRATOS:
        print(f"  {nombre:<18} {ultimo_diagnostico.get(nombre, '—')}")
