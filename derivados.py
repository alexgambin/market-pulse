"""
derivados.py — Métricas de derivados de BTC vía API pública de Binance Futures (sin clave).

Todas de la API abierta de Binance USDS-M Futures:
  - Funding rate + mark price:  GET /fapi/v1/premiumIndex
  - Open interest (nocional):    GET /fapi/v1/openInterest  +  /futures/data/openInterestHist
  - Long/short ratio (cuentas):  GET /futures/data/globalLongShortAccountRatio
  - Long/short top traders:      GET /futures/data/topLongShortAccountRatio

El funding rate es la métrica más predictiva: muy positivo = largos apalancados
sobrecalentados (riesgo de purga a la baja); muy negativo = cortos hacinados (posible
squeeze al alza). Aproxima la señal del heatmap de liquidaciones sin coste.

Autotest:  python derivados.py
"""
import asyncio
import time

import httpx

_BASE = "https://fapi.binance.com"
_SIMBOLO = "BTCUSDT"
_CABECERAS = {"User-Agent": "market-pulse-personal/1.0"}
_TTL_SEGUNDOS = 30 * 60  # los derivados se mueven rápido; caché de 30 min
_cache: dict = {"ts": 0.0, "texto": None}
ultimo_diagnostico: dict = {}


async def _get(web: httpx.AsyncClient, ruta: str, params: dict, etiqueta: str):
    """GET robusto con reintento ante fallos transitorios de red/DNS."""
    ultimo_error = None
    for intento in range(3):
        try:
            r = await web.get(f"{_BASE}{ruta}", params=params)
            if r.status_code != 200:
                ultimo_diagnostico[etiqueta] = f"FALLO — HTTP {r.status_code}"
                return None
            ultimo_diagnostico[etiqueta] = "OK"
            return r.json()
        except Exception as e:
            ultimo_error = e
            if intento < 2:
                await asyncio.sleep(0.6 * (intento + 1))
                continue
    ultimo_diagnostico[etiqueta] = f"FALLO — {type(ultimo_error).__name__}: {str(ultimo_error)[:50]}"
    return None


async def datos() -> dict:
    """Métricas de derivados de BTC en crudo (dict) para el panel de motores y el agente."""
    limites = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    out = {}
    async with httpx.AsyncClient(timeout=15, limits=limites, headers=_CABECERAS) as web:
        # 1) Funding rate (última) + mark price
        pi = await _get(web, "/fapi/v1/premiumIndex", {"symbol": _SIMBOLO}, "Funding rate")
        if pi and "lastFundingRate" in pi:
            # el funding viene como fracción (ej. 0.0001 = 0.01%); lo pasamos a % anualizado aprox
            # (cada 8h → 3 veces/día → *3*365). Guardamos el % por periodo y el anualizado.
            fr = float(pi["lastFundingRate"]) * 100  # % por periodo de 8h
            out["Funding rate"] = {"valor": round(fr, 4), "anualizado": round(fr * 3 * 365, 1)}

        # 2) Open interest actual (nocional en BTC) + histórico para la variación
        oi = await _get(web, "/fapi/v1/openInterest", {"symbol": _SIMBOLO}, "Open interest")
        oih = await _get(web, "/futures/data/openInterestHist",
                         {"symbol": _SIMBOLO, "period": "1d", "limit": 8}, "OI histórico")
        if oi and "openInterest" in oi:
            actual = float(oi["openInterest"])  # en BTC
            previo = None
            if oih and len(oih) >= 2:
                previo = float(oih[0]["sumOpenInterest"])  # ~1 semana atrás (8 días)
            out["Open interest"] = {"valor": round(actual, 0), "previo": round(previo, 0) if previo else None}

        # 3) Long/short ratio global (todas las cuentas)
        lsr = await _get(web, "/futures/data/globalLongShortAccountRatio",
                         {"symbol": _SIMBOLO, "period": "1d", "limit": 2}, "Long/short global")
        if lsr and len(lsr) >= 1:
            u = lsr[-1]
            out["Long/short global"] = {"valor": round(float(u["longShortRatio"]), 3),
                                        "largos": round(float(u["longAccount"]) * 100, 1)}

        # 4) Long/short de top traders (los grandes)
        tls = await _get(web, "/futures/data/topLongShortAccountRatio",
                         {"symbol": _SIMBOLO, "period": "1d", "limit": 2}, "Long/short top")
        if tls and len(tls) >= 1:
            u = tls[-1]
            out["Long/short top traders"] = {"valor": round(float(u["longShortRatio"]), 3),
                                             "largos": round(float(u["longAccount"]) * 100, 1)}

    return out


async def resumen() -> str:
    """Bloque de texto con las métricas de derivados de BTC, para el prompt del agente."""
    if _cache["texto"] is not None and time.time() - _cache["ts"] < _TTL_SEGUNDOS:
        return _cache["texto"]
    d = await datos()
    if not d:
        return ""
    lineas = ["== DERIVADOS DE BITCOIN (Binance Futures, en vivo) =="]
    fr = d.get("Funding rate")
    if fr:
        signo = "positivo (largos pagan: mercado caliente de largos)" if fr["valor"] > 0 else \
                "negativo (cortos pagan: cortos hacinados)" if fr["valor"] < 0 else "neutro"
        lineas.append(f"Funding rate: {fr['valor']:+.4f}% por 8h (~{fr['anualizado']:+.1f}% anual) — {signo}")
    oi = d.get("Open interest")
    if oi:
        var = ""
        if oi["previo"]:
            pct = (oi["valor"] / oi["previo"] - 1) * 100
            var = f" ({pct:+.1f}% sem)"
        lineas.append(f"Open interest: {oi['valor']:,.0f} BTC{var} — capital apalancado en juego")
    lg = d.get("Long/short global")
    if lg:
        lineas.append(f"Long/short global: {lg['valor']:.2f} ({lg['largos']:.0f}% cuentas largas) — sentimiento retail")
    lt = d.get("Long/short top traders")
    if lt:
        lineas.append(f"Long/short top traders: {lt['valor']:.2f} ({lt['largos']:.0f}% largos) — posición de los grandes")
    lineas.append("Lectura: funding muy positivo + OI alto + retail muy largo = riesgo de purga a la baja; "
                  "funding negativo + cortos hacinados = posible squeeze al alza.")
    texto = "\n".join(lineas)
    _cache["texto"] = texto
    _cache["ts"] = time.time()
    return texto


if __name__ == "__main__":
    async def _t():
        print(await resumen())
        print("\nDiagnóstico:")
        for k, v in ultimo_diagnostico.items():
            print(f"  {k:<22} {v}")
    asyncio.run(_t())
