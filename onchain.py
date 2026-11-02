"""
onchain.py — Métricas on-chain de Bitcoin vía API pública de blockchain.info (sin clave).

Charts API: https://api.blockchain.info/charts/{grafico}?timespan=30days&format=json
Devuelve series temporales. Tomamos el último valor y el previo para la variación.

Autotest:  python onchain.py
"""
import asyncio
import time

import httpx

_BASE = "https://api.blockchain.info/charts"

# etiqueta -> (gráfico de la API, unidad, descripción para el prompt)
METRICAS = {
    "Hashrate": ("hash-rate", "EH/s", "seguridad y compromiso de los mineros con la red"),
    "Direcciones activas": ("n-unique-addresses", "dir/día", "actividad de usuarios en la red"),
    "Transacciones diarias": ("n-transactions", "tx/día", "uso efectivo de la red"),
    "Comisiones totales": ("transaction-fees-usd", "USD/día", "demanda de espacio en bloque"),
}

_CABECERAS = {"User-Agent": "market-pulse-personal/1.0"}
_TTL_SEGUNDOS = 6 * 3600  # on-chain se mueve despacio; caché de 6h
_cache: dict = {"ts": 0.0, "texto": None}
ultimo_diagnostico: dict = {}


async def _una_metrica(web: httpx.AsyncClient, etiqueta: str, grafico: str):
    url = f"{_BASE}/{grafico}?timespan=30days&format=json&sampled=true"
    ultimo_error = None
    for intento in range(3):
        try:
            r = await web.get(url)
            if r.status_code != 200:
                ultimo_diagnostico[etiqueta] = f"FALLO — HTTP {r.status_code}"
                return etiqueta, None
            valores = r.json().get("values", [])
            if len(valores) < 2:
                ultimo_diagnostico[etiqueta] = "FALLO — serie vacía"
                return etiqueta, None
            valor = valores[-1]["y"]
            previo = valores[-8]["y"] if len(valores) >= 8 else valores[0]["y"]  # ~1 semana atrás
            # blockchain.info entrega el hash-rate en TH/s → a EH/s se divide por 1e6
            if grafico == "hash-rate":
                valor /= 1e6
                previo /= 1e6
            ultimo_diagnostico[etiqueta] = f"OK — {valor:.2f}"
            return etiqueta, {"valor": round(valor, 2), "previo": round(previo, 2)}
        except Exception as e:
            ultimo_error = e
            if intento < 2:
                await asyncio.sleep(0.6 * (intento + 1))
                continue
    ultimo_diagnostico[etiqueta] = f"FALLO — {type(ultimo_error).__name__}: {str(ultimo_error)[:50]}"
    return etiqueta, None


async def _todas() -> dict:
    """Consulta todas las métricas secuencialmente sobre una conexión (evita concurrencia de DNS)."""
    limites = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    out = {}
    async with httpx.AsyncClient(timeout=15, limits=limites, headers=_CABECERAS) as web:
        for et, (g, _, _) in METRICAS.items():
            etiqueta, valor = await _una_metrica(web, et, g)
            if valor:
                out[etiqueta] = valor
    return out


async def datos() -> dict:
    """Métricas on-chain en crudo (dict) para el panel de motores de cripto."""
    return await _todas()


async def resumen() -> str:
    """Bloque de texto con las métricas on-chain, para inyectar en el prompt del agente."""
    if _cache["texto"] is not None and time.time() - _cache["ts"] < _TTL_SEGUNDOS:
        return _cache["texto"]
    d = await _todas()
    if not d:
        return ""
    lineas = ["== MÉTRICAS ON-CHAIN DE BITCOIN (blockchain.info, ~1 día de retardo) =="]
    for etiqueta, (grafico, unidad, desc) in METRICAS.items():
        info = d.get(etiqueta)
        if not info:
            continue
        v, p = info["valor"], info["previo"]
        var = ""
        if p:
            pct = (v / p - 1) * 100
            var = f" ({pct:+.1f}% sem)"
        lineas.append(f"{etiqueta}: {v:,.2f} {unidad}{var} — {desc}")
    texto = "\n".join(lineas)
    _cache["texto"] = texto
    _cache["ts"] = time.time()
    return texto


if __name__ == "__main__":
    async def _t():
        print(await resumen())
        print("\nDiagnóstico:")
        for k, v in ultimo_diagnostico.items():
            print(f"  {k:<26} {v}")
    asyncio.run(_t())
