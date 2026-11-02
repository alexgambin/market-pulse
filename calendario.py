"""
calendario.py — Calendario económico semanal (JSON público de ForexFactory, sin clave).

Autotest:  python calendario.py
"""
import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_CABECERAS = {"User-Agent": "market-pulse-personal/1.0"}
_TTL_SEGUNDOS = 6 * 3600
ZONA_LOCAL = ZoneInfo("Europe/Madrid")

DIVISAS = {"USD", "JPY", "EUR", "CNY", "GBP"}
DIVISAS_MEDIO = {"USD", "JPY", "EUR"}

_cache: dict = {"ts": 0.0, "eventos": None}
ultimo_diagnostico: str = "—"

_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _parsear(crudos: list[dict]) -> list[dict]:
    eventos = []
    for e in crudos:
        try:
            impacto = (e.get("impact") or "").strip()
            divisa = (e.get("country") or "").strip()
            if impacto not in ("High", "Medium") or divisa not in DIVISAS:
                continue
            if impacto == "Medium" and divisa not in DIVISAS_MEDIO:
                continue
            momento = datetime.fromisoformat(e["date"]).astimezone(ZONA_LOCAL)
            eventos.append(
                {
                    "momento": momento,
                    "divisa": divisa,
                    "titulo": (e.get("title") or "").strip(),
                    "impacto": "alto" if impacto == "High" else "medio",
                    "consenso": (e.get("forecast") or "").strip(),
                    "previo": (e.get("previous") or "").strip(),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(eventos, key=lambda x: x["momento"])


async def _eventos() -> list[dict]:
    global ultimo_diagnostico
    if _cache["eventos"] is not None and time.time() - _cache["ts"] < _TTL_SEGUNDOS:
        return _cache["eventos"]
    try:
        async with httpx.AsyncClient(timeout=15, headers=_CABECERAS,
                                     follow_redirects=True) as web:
            r = await web.get(_URL)
            r.raise_for_status()
            eventos = _parsear(r.json())
            ultimo_diagnostico = f"OK — {len(eventos)} eventos relevantes esta semana"
    except Exception as e:
        eventos = []
        ultimo_diagnostico = f"FALLO — {type(e).__name__}: {str(e)[:60]}"
    _cache.update(ts=time.time(), eventos=eventos)
    return eventos


async def proximos(horas: int = 24) -> str:
    eventos = await _eventos()
    ahora = datetime.now(ZONA_LOCAL)
    ventana = [e for e in eventos if ahora <= e["momento"] <= ahora + timedelta(hours=horas)]
    if not ventana:
        return ""
    lineas = []
    for e in ventana[:12]:
        detalle = ""
        if e["consenso"] or e["previo"]:
            detalle = f" (cons {e['consenso'] or '—'}, prev {e['previo'] or '—'})"
        m = e["momento"]
        lineas.append(
            f"- {_DIAS[m.weekday()]} {m:%d} {m:%H:%M} · {e['divisa']} · "
            f"{e['titulo']} · impacto {e['impacto']}{detalle}"
        )
    return (
        f"== CALENDARIO ECONÓMICO (próximas {horas}h, hora de Madrid) ==\n"
        + "\n".join(lineas)
    )


async def eventos_para_prevision(horas: int = 14) -> list[dict]:
    """Eventos importantes que vienen en las próximas `horas` — para la previsión matinal.
    Devuelve la lista estructurada (no texto) para que el agente haga análisis de escenarios."""
    eventos = await _eventos()
    ahora = datetime.now(ZONA_LOCAL)
    return [e for e in eventos if ahora <= e["momento"] <= ahora + timedelta(hours=horas)]


# ── Detección de datos recién publicados (para la reacción post-dato) ──
# El feed semanal de ForexFactory NO trae el "actual". Para el valor real consultamos
# el feed de faireconomy que sí lo incluye una vez publicado (mismo host, campo "actual").
_ya_reaccionado: set = set()  # ids de eventos ya procesados, para no repetir


def _clave_evento(e: dict) -> str:
    return f"{e['titulo']}|{e['momento'].isoformat()}"


async def recien_publicados(margen_min: int = 20) -> list[dict]:
    """Eventos importantes cuyo dato REAL acaba de salir (en los últimos `margen_min` min)
    y aún no se han procesado. Devuelve los que tienen 'actual' disponible para reaccionar."""
    global _ya_reaccionado
    ahora = datetime.now(ZONA_LOCAL)
    # re-descarga fresca del feed (que ya puede traer el actual tras la publicación)
    try:
        async with httpx.AsyncClient(timeout=15, headers=_CABECERAS,
                                     follow_redirects=True) as web:
            r = await web.get(_URL)
            r.raise_for_status()
            crudos = r.json()
    except Exception:
        return []

    listos = []
    for e in crudos:
        try:
            impacto = (e.get("impact") or "").strip()
            divisa = (e.get("country") or "").strip()
            if impacto not in ("High", "Medium") or divisa not in DIVISAS:
                continue
            if impacto == "Medium" and divisa not in DIVISAS_MEDIO:
                continue
            momento = datetime.fromisoformat(e["date"]).astimezone(ZONA_LOCAL)
            actual = (e.get("actual") or "").strip()
            if not actual:
                continue  # aún no publicado
            # ¿salió hace poco y no lo hemos procesado?
            if not (timedelta(0) <= ahora - momento <= timedelta(minutes=margen_min)):
                continue
            titulo = (e.get("title") or "").strip()
            clave = f"{titulo}|{momento.isoformat()}"
            if clave in _ya_reaccionado:
                continue
            _ya_reaccionado.add(clave)
            listos.append({
                "momento": momento, "divisa": divisa, "titulo": titulo,
                "impacto": "alto" if impacto == "High" else "medio",
                "consenso": (e.get("forecast") or "").strip(),
                "previo": (e.get("previous") or "").strip(),
                "actual": actual,
            })
        except (KeyError, ValueError, TypeError):
            continue
    return listos


if __name__ == "__main__":
    print(asyncio.run(proximos(72)) or "(sin eventos en la ventana)")
    print(f"\nDiagnóstico: {ultimo_diagnostico}")
