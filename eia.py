"""
eia.py — Datos físicos de energía de la EIA (API v2 oficial, requiere EIA_API_KEY).

El dato duro que a los hilos de crudo les faltaba:
  - Inventarios semanales de crudo comercial (el evento del miércoles).
  - Inventarios de gasolina.
  - Producción de crudo de EEUU.

Se publica los miércoles con datos del viernes anterior. Cacheamos 12 h. Si
falta la clave o falla la API, devuelve "" y el sistema sigue sin romperse.

Autotest:  python eia.py
"""
import asyncio
import os
import time

import httpx

_BASE = "https://api.eia.gov/v2"

# etiqueta → (ruta de la serie, id de la serie, unidad, descripción)
# etiqueta → (ruta, id de serie, unidad, frecuencia, descripción)
SERIES = {
    "Inventarios crudo": (
        "petroleum/stoc/wstk", "WCESTUS1", "kb", "weekly", "existencias comerciales de crudo"),
    "Inventarios gasolina": (
        "petroleum/stoc/wstk", "WGTSTUS1", "kb", "weekly", "existencias de gasolina"),
    "Producción crudo EEUU": (
        "petroleum/crd/crpdn", "MCRFPUS2", "kb/d", "monthly", "producción de crudo de EEUU"),
}

_TTL_SEGUNDOS = 12 * 3600
_cache: dict = {"ts": 0.0, "texto": None}
ultimo_diagnostico: dict = {}


async def _serie(web: httpx.AsyncClient, etiqueta: str, ruta: str, sid: str,
                 frecuencia: str, clave: str):
    url = (
        f"{_BASE}/{ruta}/data/?api_key={clave}&frequency={frecuencia}"
        f"&data[0]=value&facets[series][]={sid}"
        f"&sort[0][column]=period&sort[0][direction]=desc&length=6"
    )
    try:
        r = await web.get(url)
        if r.status_code != 200:
            ultimo_diagnostico[etiqueta] = f"FALLO — HTTP {r.status_code}: {r.text[:60]}"
            return etiqueta, None
        datos = r.json().get("response", {}).get("data", [])
        puntos = [(d["period"], float(d["value"])) for d in datos if d.get("value") is not None]
        if not puntos:
            ultimo_diagnostico[etiqueta] = "FALLO — sin observaciones"
            return etiqueta, None
        fecha, valor = puntos[0]
        previo = puntos[1][1] if len(puntos) > 1 else None
        ultimo_diagnostico[etiqueta] = f"OK — {valor:,.0f} ({fecha})"
        return etiqueta, {"valor": valor, "previo": previo, "fecha": fecha}
    except Exception as e:
        ultimo_diagnostico[etiqueta] = f"FALLO — {type(e).__name__}: {str(e)[:50]}"
        return etiqueta, None


async def datos() -> dict:
    """Valores de energía física en crudo (dict) para el panel de motores."""
    clave = os.environ.get("EIA_API_KEY", "")
    if not clave:
        return {}
    async with httpx.AsyncClient(timeout=15) as web:
        resultados = dict(await asyncio.gather(
            *(_serie(web, et, ruta, sid, frec, clave) for et, (ruta, sid, _, frec, _) in SERIES.items())
        ))
    return {et: d for et, d in resultados.items() if d}


async def energia() -> str:
    """Bloque de texto con los datos físicos de energía, listo para un prompt."""
    clave = os.environ.get("EIA_API_KEY", "")
    if not clave:
        return ""
    if _cache["texto"] is not None and time.time() - _cache["ts"] < _TTL_SEGUNDOS:
        return _cache["texto"]

    async with httpx.AsyncClient(timeout=15) as web:
        resultados = dict(await asyncio.gather(
            *(_serie(web, et, ruta, sid, frec, clave) for et, (ruta, sid, _, frec, _) in SERIES.items())
        ))

    lineas = []
    for etiqueta, (_, _, unidad, _, desc) in SERIES.items():
        d = resultados.get(etiqueta)
        if not d:
            continue
        delta = ""
        if d["previo"] is not None:
            dif = d["valor"] - d["previo"]
            frec = SERIES[etiqueta][3]
            perico = "semanal" if frec == "weekly" else "mensual"
            signo = "acumulación" if dif > 0 else "reducción"
            delta = f" ({dif:+,.0f} {unidad}, {signo} {perico})"
        lineas.append(f"{etiqueta}: {d['valor']:,.0f} {unidad}{delta} — {desc} ({d['fecha']})")

    if not lineas:
        texto = ""
    else:
        texto = (
            "== ENERGÍA FÍSICA (EIA, semanal — publicado los miércoles) ==\n"
            + "\n".join(lineas)
        )
    _cache.update(ts=time.time(), texto=texto)
    return texto


if __name__ == "__main__":
    if not os.environ.get("EIA_API_KEY"):
        print("Falta EIA_API_KEY en el entorno. Añádela a ~/.zshrc y haz source.")
    else:
        print(asyncio.run(energia()) or "(sin datos)")
        print("\nDiagnóstico:")
        for et in SERIES:
            print(f"  {et:<22} {ultimo_diagnostico.get(et, '—')}")
