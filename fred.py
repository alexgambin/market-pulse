"""
fred.py — Datos macro estructurales de FRED (API de la Fed de St. Louis).

Los motores que a la cinta de precios le faltaban:
  - Tipo real a 10 años (DFII10): el driver nº1 del oro.
  - Breakeven de inflación a 10 años (T10YIE): expectativas de inflación.
  - OAT-Bund a 10 años (calculado): termómetro del riesgo soberano del euro.

Requiere la variable de entorno FRED_API_KEY. Se publica con un día de retardo;
cacheamos 12 h. Si falta la clave o falla la API, devuelve "" y el sistema sigue.

Autotest:  python fred.py
"""
import asyncio
import os
import time

import httpx

_BASE = "https://api.stlouisfed.org/fred/series/observations"

# etiqueta → (id de serie FRED, unidad, descripción corta)
SERIES = {
    "Tipo real 10A (EEUU)": ("DFII10", "%", "TIPS a 10 años, driver del oro"),
    "Breakeven inflación 10A": ("T10YIE", "%", "expectativa de inflación a 10 años"),
    "Bono Francia 10A": ("IRLTLT01FRM156N", "%", "OAT a 10 años"),
    "Bono Alemania 10A": ("IRLTLT01DEM156N", "%", "Bund a 10 años"),
}

_TTL_SEGUNDOS = 12 * 3600
_cache: dict = {"ts": 0.0, "texto": None}
ultimo_diagnostico: dict = {}


async def _ultimo_valor(web: httpx.AsyncClient, etiqueta: str, serie: str, clave: str):
    url = (
        f"{_BASE}?series_id={serie}&api_key={clave}&file_type=json"
        f"&sort_order=desc&limit=6"
    )
    # Reintento ante fallos transitorios de red/DNS (el resolvedor a veces no
    # resuelve el host bajo varias peticiones; un segundo intento casi siempre pasa).
    ultimo_error = None
    for intento in range(3):
        try:
            r = await web.get(url)
            if r.status_code != 200:
                ultimo_diagnostico[etiqueta] = f"FALLO — HTTP {r.status_code}"
                return etiqueta, None
            obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", "", None)]
            if not obs:
                ultimo_diagnostico[etiqueta] = "FALLO — sin observaciones válidas"
                return etiqueta, None
            valor = float(obs[0]["value"])
            previo = float(obs[1]["value"]) if len(obs) > 1 else None
            ultimo_diagnostico[etiqueta] = f"OK — {valor:.2f} ({obs[0]['date']})"
            return etiqueta, {"valor": valor, "previo": previo, "fecha": obs[0]["date"]}
        except Exception as e:
            ultimo_error = e
            if intento < 2:
                await asyncio.sleep(0.6 * (intento + 1))  # 0.6s, 1.2s
                continue
    ultimo_diagnostico[etiqueta] = f"FALLO — {type(ultimo_error).__name__}: {str(ultimo_error)[:50]}"
    return etiqueta, None


async def _todas_las_series(clave: str) -> dict:
    """Consulta todas las series de FRED sobre UNA conexión, en secuencia.
    Evita la concurrencia de DNS que hacía fallar peticiones simultáneas al mismo host:
    el socket se reutiliza y el nombre se resuelve una sola vez."""
    limites = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    resultados = {}
    async with httpx.AsyncClient(timeout=15, limits=limites) as web:
        for et, (s, _, _) in SERIES.items():
            etiqueta, valor = await _ultimo_valor(web, et, s, clave)
            resultados[etiqueta] = valor
    return resultados


async def datos() -> dict:
    """Valores macro en crudo (dict) para el panel de motores. Reutiliza la lógica de macro()."""
    clave = os.environ.get("FRED_API_KEY", "")
    if not clave:
        return {}
    resultados = await _todas_las_series(clave)
    out = {}
    for etiqueta, d in resultados.items():
        if d:
            out[etiqueta] = d
    fr, de = resultados.get("Bono Francia 10A"), resultados.get("Bono Alemania 10A")
    if fr and de:
        out["Spread OAT-Bund"] = {"valor": round((fr["valor"] - de["valor"]) * 100, 0),
                                  "previo": None, "fecha": fr["fecha"]}
    return out


async def macro() -> str:
    """Bloque de texto con los datos macro estructurales, listo para un prompt."""
    clave = os.environ.get("FRED_API_KEY", "")
    if not clave:
        return ""
    if _cache["texto"] is not None and time.time() - _cache["ts"] < _TTL_SEGUNDOS:
        return _cache["texto"]

    resultados = await _todas_las_series(clave)

    lineas = []
    for etiqueta, (serie, unidad, desc) in SERIES.items():
        d = resultados.get(etiqueta)
        if not d:
            continue
        delta = ""
        if d["previo"] is not None:
            dif = d["valor"] - d["previo"]
            delta = f" ({dif:+.2f} vs previo)"
        lineas.append(f"{etiqueta}: {d['valor']:.2f}{unidad}{delta} — {desc} ({d['fecha']})")

    # Spread OAT-Bund derivado, si tenemos los dos bonos
    fr, de = resultados.get("Bono Francia 10A"), resultados.get("Bono Alemania 10A")
    if fr and de:
        spread = (fr["valor"] - de["valor"]) * 100  # en puntos básicos
        alerta = " — ATENCIÓN: fragmentación" if spread >= 80 else ""
        lineas.append(
            f"Spread OAT-Bund: {spread:.0f} pb{alerta} — riesgo soberano del euro"
        )

    if not lineas:
        texto = ""
    else:
        texto = (
            "== MACRO ESTRUCTURAL (FRED, ~1 día de retardo) ==\n" + "\n".join(lineas)
        )
    _cache.update(ts=time.time(), texto=texto)
    return texto


if __name__ == "__main__":
    if not os.environ.get("FRED_API_KEY"):
        print("Falta FRED_API_KEY en el entorno. Añádela a ~/.zshrc y haz source.")
    else:
        print(asyncio.run(macro()) or "(sin datos)")
        print("\nDiagnóstico:")
        for et in SERIES:
            print(f"  {et:<26} {ultimo_diagnostico.get(et, '—')}")
