"""
main.py — Market Pulse v2: servidor y orquestación.

Arranque:
    export ANTHROPIC_API_KEY="tu-clave"
    uvicorn main:app --reload --port 8000

Cada pasada: recolecta RSS → guarda con dedup → el agente procesa las
pendientes contra su memoria de hilos → guarda la síntesis.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db
import consumo_ia
import fuentes
import informes
import memoria
import precios
import validation
from agente import ejecutar_agente, analizar_evento
import calendario

ZONA = "Europe/Madrid"
# Pasadas de ingesta + agente (las de 06:45 y 21:45 dejan la memoria fresca
# para los informes de 07:00 y 22:00 del Paso 3).
HORARIO_PASADAS = [(6, 45), (10, 0), (14, 0), (18, 0), (21, 45)]

# ── Frecuencia automática acotada ───────────────────────────────────────────
# Tras una pasada "caliente" se programa UNA extra. Ajusta a gusto:
UMBRAL_RELEVANTES = 12      # noticias que superan el triaje para considerarla caliente
RETRASO_EXTRA_HORAS = 2     # cuándo corre la extra
MAX_EXTRAS_DIA = 3          # tope diario de extras
MARGEN_ANCLA_MIN = 45       # no programar extras a menos de esto de una pasada fija
ZONA_TZ = ZoneInfo(ZONA)

# ── Vigilante de rupturas (cada 15 min, solo cinta, sin LLM) ────────────────
UMBRALES_RUPTURA = {"WTI": 2.0, "USD/JPY": 0.8, "EUR/USD": 0.6, "Oro": 1.5,
                    "GBP/USD": 0.7, "AUD/USD": 0.7, "USD/CHF": 0.6, "USD/CAD": 0.6,
                    "BTC": 4.0, "ETH": 5.0}   # % desde la última pasada (cripto más volátil)
ENFRIAMIENTO_RUPTURA_MIN = 45

# ── Detector de convergencia (tras cada pasada) ─────────────────────────────
CONVERGENCIA_HILOS = 3       # hilos de relevancia 4-5 movidos en la misma pasada
ENFRIAMIENTO_CONVERGENCIA_H = 4

scheduler = AsyncIOScheduler()

estado = {
    "ejecutando": False,
    "ultima_pasada": None,
    "ultimo_error": None,
    "nuevas_ultima_pasada": 0,
    "salud_fuentes": {},
    "ultimo_informe": None,
    "extras": {"fecha": None, "n": 0},
    "proxima_extra": None,
    "base_vigilante": None,
    "ultima_ruptura": None,
    "ultima_convergencia": None,
}


async def pasada(componente="otro", subcomponente=None, metadatos=None):
    """Una vuelta completa: ingesta → agente → síntesis."""
    if estado["ejecutando"]:
        return
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        estado["ultimo_error"] = "ANTHROPIC_API_KEY no configurada"
        return

    estado["ejecutando"] = True
    estado["ultimo_error"] = None
    estado["proxima_extra"] = None
    inicio_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    contexto = consumo_ia.iniciar_ejecucion(componente, subcomponente, metadatos)
    error_ejecucion = None
    try:
        # feedparser es bloqueante: fuera del event loop
        noticias, salud = await asyncio.to_thread(fuentes.recolectar)
        estado["salud_fuentes"] = salud
        estado["nuevas_ultima_pasada"] = db.guardar_noticias(noticias)

        pendientes = db.noticias_pendientes(limite=40)
        consumo_ia.contar_noticias(contexto, noticias_consideradas=len(pendientes))
        if pendientes:
            sintesis = await ejecutar_agente(api_key, pendientes, contexto=contexto)
            db.guardar_analisis(sintesis, len(pendientes))
            db.marcar_procesadas([n["id"] for n in pendientes])

        estado["ultima_pasada"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        estado["base_vigilante"] = None  # el vigilante se re-basa tras cada pasada
        _evaluar_pasada_extra(inicio_iso)
        _detectar_convergencia(inicio_iso)
    except Exception as e:
        error_ejecucion = e
        estado["ultimo_error"] = str(e)[:300]
    finally:
        estado["ejecutando"] = False
        consumo_ia.finalizar_ejecucion(contexto, error_ejecucion is None, error_ejecucion)


async def informe(tipo: str, origen: str = "programado"):
    """Genera el informe (overnight/cierre/ahora), lo envía por Telegram y lo archiva."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        estado["ultimo_error"] = "ANTHROPIC_API_KEY no configurada"
        return
    try:
        texto = await informes.generar_informe(api_key, tipo, contexto=consumo_ia.Contexto(
            {"overnight": "informe_manana", "cierre": "informe_noche", "ahora": "informe_ahora"}.get(tipo, "otro"),
            origen, {"tipo": tipo}))
        await informes.enviar_telegram(texto)
        db.guardar_analisis(f"[Informe {tipo}]\n\n{texto}", 0)
        estado["ultimo_informe"] = (
            f'{tipo} · {datetime.now(timezone.utc).isoformat(timespec="seconds")}'
        )
        estado["ultimo_error"] = None
    except Exception as e:
        estado["ultimo_error"] = f"informe {tipo}: {e}"[:300]


def _hay_ruptura(base: dict, actual: dict) -> list[str]:
    """Compara la instantánea con la base y devuelve los avisos que superan umbral."""
    avisos = []
    for nombre, umbral in UMBRALES_RUPTURA.items():
        b, a = base.get(nombre), actual.get(nombre)
        if not b or not a:
            continue
        salto = (a["ultimo"] / b["ultimo"] - 1) * 100
        if abs(salto) >= umbral:
            dec = 4 if nombre in ("EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF", "USD/CAD") else 2
            avisos.append(
                f"{nombre} {salto:+.2f}% desde la última pasada "
                f"({b['ultimo']:.{dec}f} → {a['ultimo']:.{dec}f})"
            )
    return avisos


async def vigilante():
    """Cada 15 min: si la cinta salta más del umbral, pasada inmediata + aviso."""
    if estado["ejecutando"]:
        return
    if estado["ultima_ruptura"]:
        desde = datetime.fromisoformat(estado["ultima_ruptura"])
        if (datetime.now(timezone.utc) - desde).total_seconds() < ENFRIAMIENTO_RUPTURA_MIN * 60:
            return

    actual = await precios.instantanea(list(UMBRALES_RUPTURA))
    if not actual:
        return
    base = estado["base_vigilante"]
    if base is None:
        estado["base_vigilante"] = actual
        return

    avisos = _hay_ruptura(base, actual)
    if not avisos:
        return
    estado["ultima_ruptura"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    estado["base_vigilante"] = actual
    print(f"[vigilante] ruptura: {'; '.join(avisos)} — lanzando pasada")
    try:
        await informes.enviar_telegram("⚠ Ruptura de cinta: " + "; ".join(avisos)
                                       + ". Lanzando pasada de análisis.")
    except Exception:
        pass  # sin Telegram configurado, el vigilante sigue actuando
    asyncio.create_task(pasada(componente="pasada_vigilante", subcomponente="ruptura",
        metadatos={"motivo": "; ".join(avisos), "disparos": [
            {"instrumento": n, "variacion_pct": (actual[n]["ultimo"] / base[n]["ultimo"] - 1) * 100}
            for n, umbral in UMBRALES_RUPTURA.items()
            if base.get(n) and actual.get(n)
            and abs((actual[n]["ultimo"] / base[n]["ultimo"] - 1) * 100) >= umbral]}))


def _detectar_convergencia(inicio_iso: str):
    """≥N hilos de relevancia 4-5 movidos en la misma pasada = momento de régimen."""
    if estado["ultima_convergencia"]:
        desde = datetime.fromisoformat(estado["ultima_convergencia"])
        if (datetime.now(timezone.utc) - desde).total_seconds() < ENFRIAMIENTO_CONVERGENCIA_H * 3600:
            return
    movidos = [h["nombre"] for h in memoria.listar_hilos()
               if h["relevancia"] >= 4 and h["actualizado"] >= inicio_iso]
    if len(movidos) < CONVERGENCIA_HILOS:
        return
    estado["ultima_convergencia"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[sistema] convergencia: {len(movidos)} hilos críticos movidos "
          f"({', '.join(movidos[:4])}…) — informe inmediato")
    try:
        asyncio.create_task(informe("ahora", origen="convergencia"))
    except RuntimeError:  # sin event loop (solo posible fuera del servidor, p. ej. en tests)
        pass


def _cerca_de_ancla(objetivo) -> bool:
    """¿Cae el objetivo demasiado cerca de una pasada fija de hoy o mañana?"""
    for dias in (0, 1):
        base = (objetivo + timedelta(days=dias)).date() if dias else objetivo.date()
        for h, mi in HORARIO_PASADAS:
            ancla = objetivo.replace(year=base.year, month=base.month, day=base.day,
                                     hour=h, minute=mi, second=0, microsecond=0)
            if abs((objetivo - ancla).total_seconds()) < MARGEN_ANCLA_MIN * 60:
                return True
    return False


def _evaluar_pasada_extra(inicio_iso: str):
    """Regla acotada: pasada extra si la última fue caliente (volumen o hilo crítico)."""
    from agente import info_pasada

    relevantes = info_pasada.get("relevantes", 0)
    r5_movido = any(
        h["relevancia"] == 5 and h["actualizado"] >= inicio_iso
        for h in memoria.listar_hilos()
    )
    if relevantes < UMBRAL_RELEVANTES and not r5_movido:
        return

    hoy = date.today().isoformat()
    if estado["extras"]["fecha"] != hoy:
        estado["extras"] = {"fecha": hoy, "n": 0}
    if estado["extras"]["n"] >= MAX_EXTRAS_DIA:
        return

    objetivo = datetime.now(ZONA_TZ) + timedelta(hours=RETRASO_EXTRA_HORAS)
    if _cerca_de_ancla(objetivo):
        return  # la pasada fija cercana ya lo cubrirá

    scheduler.add_job(pasada, "date", run_date=objetivo,
                      id=f"pasada_extra_{int(objetivo.timestamp())}",
                      misfire_grace_time=600,
                      kwargs={"componente": "pasada_extra",
                              "subcomponente": "hilo_critico" if r5_movido else "noticias_relevantes",
                              "metadatos": {"noticias_relevantes": relevantes, "hilo_critico": r5_movido}})
    estado["extras"]["n"] += 1
    estado["proxima_extra"] = objetivo.isoformat(timespec="minutes")
    motivo = "hilo crítico movido" if r5_movido else f"{relevantes} noticias relevantes"
    print(f"[sistema] pasada extra programada a las {objetivo:%H:%M} ({motivo}; "
          f"{estado['extras']['n']}/{MAX_EXTRAS_DIA} hoy)")


async def validar_predicciones():
    """Validación independiente de las pasadas del agente; no usa LLM."""
    try:
        resumen = await asyncio.to_thread(validation.ejecutar_validacion)
        estado["validacion"] = {"ultima_ejecucion": db._ahora(), "estados": resumen, "error": None}
        print(f"[validation] {resumen}")
    except Exception as exc:
        estado["validacion"] = {"ultima_ejecucion": db._ahora(), "error": str(exc)}
        print(f"[validation] error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    consumo_ia.init_consumo()
    memoria.init_memoria()
    from positioning.repository import initialize as init_positioning
    init_positioning(db.DB_PATH)
    scheduler.add_job(validar_predicciones,
                      CronTrigger(minute=10, timezone=ZONA), id="validacion_predicciones",
                      replace_existing=True, coalesce=True, max_instances=1,
                      misfire_grace_time=3600)
    for hora, minuto in HORARIO_PASADAS:
        scheduler.add_job(
            pasada,
            CronTrigger(hour=hora, minute=minuto, timezone=ZONA),
            id=f"pasada_{hora:02d}{minuto:02d}",
            kwargs={"componente": "pasada_programada", "subcomponente": f"{hora:02d}:{minuto:02d}"},
            replace_existing=True,
        )
    scheduler.add_job(vigilante, CronTrigger(minute="*/15", timezone=ZONA),
                      id="vigilante", replace_existing=True)
    scheduler.add_job(informe, CronTrigger(hour=7, minute=0, timezone=ZONA),
                      id="informe_overnight", args=["overnight"], replace_existing=True)
    scheduler.add_job(informe, CronTrigger(hour=22, minute=0, timezone=ZONA),
                      id="informe_cierre", args=["cierre"], replace_existing=True)

    async def escaneo_polymarket():
        import polymarket
        try:
            await polymarket.escanear()
        except Exception as e:
            print(f"[POLYMARKET] error en escaneo: {e}")

    scheduler.add_job(escaneo_polymarket, CronTrigger(minute="*/15", timezone=ZONA),
                      id="polymarket_scan", replace_existing=True)

    async def recap_dominical():
        """Cada domingo por la mañana: repaso de las predicciones que vencen esta semana."""
        try:
            pendientes = await asyncio.to_thread(memoria.recap_semanal)
            if not pendientes:
                return
            lineas = ["📋 Recap semanal — predicciones que vencen en 7 días:\n"]
            for p in pendientes:
                pc = round(p["probabilidad"] * 100)
                marca = " (vence hoy)" if p.get("vencida") else f" ({p['horizonte']})"
                lineas.append(f"• {pc}% — {p['afirmacion']}{marca}")
            await informes.enviar_telegram("\n".join(lineas))
        except Exception as e:
            print(f"[RECAP] error: {e}")

    scheduler.add_job(recap_dominical, CronTrigger(day_of_week="sun", hour=9, minute=0, timezone=ZONA),
                      id="recap_dominical", replace_existing=True)

    async def prevision_eventos():
        """Antes del briefing matinal: análisis de escenarios de los eventos importantes del día."""
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return
        try:
            eventos = await calendario.eventos_para_prevision(horas=14)
            if not eventos:
                return
            bloques = ["📅 Previsión de eventos de hoy — escenarios antes de los datos:\n"]
            for ev in eventos[:6]:  # tope para acotar coste
                analisis = await analizar_evento(api_key, ev, "prevision", contexto=consumo_ia.Contexto("evento_prevision", ev.get("titulo")))
                bloques.append(f"⏰ {ev['momento']:%H:%M} · {ev['titulo']} ({ev['divisa']})\n{analisis}\n")
            await informes.enviar_telegram("\n".join(bloques))
        except Exception as e:
            print(f"[PREVISION] error: {e}")

    scheduler.add_job(prevision_eventos, CronTrigger(hour=6, minute=50, timezone=ZONA),
                      id="prevision_eventos", replace_existing=True)

    async def reaccion_eventos():
        """Cada 5 min: si acaba de salir un dato importante, interpreta la reacción."""
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return
        try:
            publicados = await calendario.recien_publicados(margen_min=20)
            for ev in publicados:
                analisis = await analizar_evento(api_key, ev, "reaccion", contexto=consumo_ia.Contexto("evento_reaccion", ev.get("titulo")))
                sorpresa = f"real {ev.get('actual')} vs esperado {ev.get('consenso') or '—'}"
                await informes.enviar_telegram(
                    f"📊 DATO PUBLICADO · {ev['titulo']} ({ev['divisa']})\n{sorpresa}\n\n{analisis}")
        except Exception as e:
            print(f"[REACCION] error: {e}")

    scheduler.add_job(reaccion_eventos, CronTrigger(minute="*/5", timezone=ZONA),
                      id="reaccion_eventos", replace_existing=True)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Market Pulse v2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # solo corre en localhost; la clave x-mp-key es la puerta real
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/mercados.html")


@app.get("/api/polymarket")
async def api_polymarket():
    """Estado del paper trading de Polymarket: posiciones, stats, equity."""
    import polymarket
    return await asyncio.to_thread(polymarket.resumen)


@app.post("/api/polymarket/escanear")
async def api_polymarket_escanear():
    """Dispara un escaneo manual de Polymarket (abre/resuelve posiciones en paper)."""
    import polymarket
    return await polymarket.escanear()


@app.get("/api/puente/senales")
async def api_puente_senales():
    """Señales fundamentales registradas (Market Pulse vs precio de mercado)."""
    import puente
    return await asyncio.to_thread(puente.resumen_senales)


@app.get("/api/puente/evaluar")
async def api_puente_evaluar(pregunta: str, precio_yes: int, fecha: str = None):
    """Evalúa un mercado puntual contra Market Pulse (para probar el puente)."""
    import puente
    r = await puente.evaluar(pregunta, precio_yes, fecha)
    return r or {"señal": None, "motivo": "Market Pulse no cubre este activo o no se pudo extraer nivel"}


@app.get("/clasico")
async def dashboard_clasico():
    return FileResponse("static/index.html")


@app.get("/api/estado")
async def api_estado():
    return estado


@app.get("/api/hilos")
async def api_hilos():
    return memoria.listar_hilos()


@app.get("/api/hilos/{hilo_id}")
async def api_hilo(hilo_id: int):
    return memoria.leer_hilo(hilo_id)


@app.get("/api/sintesis")
async def api_sintesis(limite: int = 3):
    return db.ultimos_analisis(limite)


@app.get("/api/noticias")
async def api_noticias(limite: int = 30):
    return db.ultimas_noticias(limite)


@app.post("/api/ejecutar")
async def api_ejecutar():
    if estado["ejecutando"]:
        return {"ok": False, "motivo": "ya hay una pasada en curso"}
    asyncio.create_task(pasada(componente="pasada_manual", subcomponente="api"))
    return {"ok": True}


@app.post("/api/x-digest")
async def api_x_digest(payload: dict, x_mp_key: str = Header(default="")):
    """Recibe el digest de X desde el carril del navegador y lo vuelca como noticias."""
    clave = os.environ.get("MP_INGEST_KEY", "")
    if not clave:
        raise HTTPException(503, "MP_INGEST_KEY no configurada en el servidor")
    if x_mp_key != clave:
        raise HTTPException(401, "clave de ingesta incorrecta")
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise HTTPException(422, "formato: {'items': [...]} ")
    noticias = []
    for it in items[:60]:
        texto = str(it.get("texto", "")).strip()
        usuario = str(it.get("usuario", "?")).lstrip("@")
        if not texto:
            continue
        noticias.append(
            {
                "fuente": f"X/@{usuario}",
                "titular": texto[:140],
                "resumen": texto[:500],
                "url": it.get("url"),
                "publicado": it.get("fecha"),
            }
        )
    nuevas = db.guardar_noticias(noticias)
    return {"ok": True, "recibidas": len(noticias), "nuevas": nuevas}


@app.get("/api/cot")
async def api_cot():
    import cot as _cot
    return {nombre: await _cot.historico(nombre) for nombre in _cot.CONTRATOS}


@app.get("/api/cot/overview")
async def api_cot_overview():
    from positioning.presentation import overview
    return await overview()


@app.get("/api/velas")
async def api_velas(inst: str, tf: str = "1D"):
    import velas as _velas
    return {"instrumento": inst, "tf": tf, "velas": await _velas.velas(inst, tf)}


# Qué hilos (por nombre) pertenecen a cada mercado — coincidencia por palabras clave
_MERCADOS = {
    "oro": ["oro", "gold", "tipos reales", "refugio"],
    "petroleo": ["ormuz", "crudo", "wti", "brent", "opep", "opec", "houthi", "refino",
                 "rins", "iraq", "uae", "energ", "bab el-mandeb", "kazakh", "canad"],
    "forex": ["boj", "jpy", "yen", "eur", "euro", "bce", "ecb", "dxy", "dólar", "divisa",
              "burnham", "gbp", "france", "francia", "soberano"],
    "macro": ["fed", "inflación", "tipos", "recesión", "renta variable", "tech", "s&p",
              "nasdaq", "china", "ucrania", "defensa"],
    "cripto": ["bitcoin", "btc", "ether", "eth", "cripto", "crypto", "etf de bitcoin",
               "on-chain", "onchain", "halving", "stablecoin", "sec", "blackrock"],
}


@app.get("/api/mercado/{clave}")
async def api_mercado(clave: str):
    """Todo lo relevante de un mercado en una llamada: hilos, COT y predicciones."""
    claves = _MERCADOS.get(clave, [])
    hilos = [
        h for h in memoria.listar_hilos()
        if any(k in h["nombre"].lower() for k in claves)
    ]
    import cot as _cot
    cot_data = {}
    mapa_cot = {"oro": ["Oro"], "plata": ["Plata"], "petroleo": ["WTI"],
                "forex": ["Yen (6J vs USD)", "Euro (6E vs USD)"], "macro": [],
                "cripto": ["Bitcoin (CME)"]}
    for nombre in mapa_cot.get(clave, []):
        if nombre in _cot.CONTRATOS:
            cot_data[nombre] = await _cot.historico(nombre)
    inst_mercado = {"oro": ["Oro"], "petroleo": ["WTI", "Brent"],
                    "forex": ["USD/JPY", "EUR/USD", "GBP/USD", "AUD/USD", "USD/CHF", "USD/CAD"],
                    "macro": ["S&P 500", "VIX", "US 10A"], "cripto": ["BTC", "ETH"]}
    abiertas = [
        p for p in memoria.predicciones_abiertas()
        if (p.get("instrumento") or "") in inst_mercado.get(clave, [])
    ]
    return {"clave": clave, "hilos": hilos, "cot": cot_data, "predicciones": abiertas}


def _motor(nombre, valor, unidad, previo, lectura_sube, lectura_baja, fecha=None):
    """Construye un motor con su dirección y lectura direccional para el activo."""
    m = {"nombre": nombre, "valor": valor, "unidad": unidad, "fecha": fecha}
    if previo is not None and valor is not None:
        dif = valor - previo
        m["delta"] = round(dif, 4)
        m["direccion"] = "sube" if dif > 0 else ("baja" if dif < 0 else "plano")
        m["lectura"] = lectura_sube if dif > 0 else (lectura_baja if dif < 0 else "sin cambio")
    else:
        m["delta"] = None
        m["direccion"] = "plano"
        m["lectura"] = ""
    return m


@app.get("/api/motores/{clave}")
async def api_motores(clave: str):
    """Los drivers reales de cada mercado, con su valor actual y qué implican para el activo."""
    import fred as _fred, cot as _cot, precios as _precios
    motores = []

    if clave == "oro":
        fd = await _fred.datos()
        tr = fd.get("Tipo real 10A (EEUU)")
        if tr:
            motores.append(_motor("Tipo real 10A", tr["valor"], "%", tr["previo"],
                "desfavorable: sube el coste de oportunidad del oro",
                "favorable: baja el coste de oportunidad, empuja el oro", tr["fecha"]))
        bk = fd.get("Breakeven inflación 10A")
        if bk:
            motores.append(_motor("Breakeven inflación", bk["valor"], "%", bk["previo"],
                "favorable: más presión inflacionista, refugio",
                "neutral-negativo: menos cobertura de inflación", bk["fecha"]))
        cot_oro = await _cot.historico("Oro")
        if cot_oro:
            u = cot_oro[-1]; p = cot_oro[-2]["neto"] if len(cot_oro) > 1 else None
            motores.append(_motor("COT fondos gestionados", u["neto"], "", p,
                "más largos: momentum alcista, ojo a saturación",
                "recortan largos: se enfría el posicionamiento", u["fecha"]))
        dxy = await _precios.instantanea(["DXY"])
        d = dxy.get("DXY")
        if d and d.get("var_1d") is not None:
            sube = d["var_1d"] > 0
            motores.append({"nombre": "Dólar (DXY)", "valor": round(d["ultimo"], 2), "unidad": "",
                "delta": round(d["var_1d"], 2), "direccion": "sube" if sube else "baja",
                "lectura": "desfavorable: dólar fuerte pesa sobre el oro" if sube
                           else "favorable: dólar débil impulsa el oro", "fecha": "en vivo"})

    elif clave == "plata":
        fd = await _fred.datos()
        tr = fd.get("Tipo real 10A (EEUU)")
        if tr:
            motores.append(_motor("Tipo real 10A", tr["valor"], "%", tr["previo"],
                "desfavorable: sube el coste de oportunidad de la plata",
                "favorable: baja el coste de oportunidad, empuja la plata", tr["fecha"]))
        bk = fd.get("Breakeven inflación 10A")
        if bk:
            motores.append(_motor("Breakeven inflación", bk["valor"], "%", bk["previo"],
                "favorable: más presión inflacionista, refugio",
                "neutral-negativo: menos cobertura de inflación", bk["fecha"]))
        cot_plata = await _cot.historico("Plata")
        if cot_plata:
            u = cot_plata[-1]; p = cot_plata[-2]["neto"] if len(cot_plata) > 1 else None
            motores.append(_motor("COT fondos gestionados", u["neto"], "", p,
                "más largos: momentum alcista, ojo a saturación",
                "recortan largos: se enfría el posicionamiento", u["fecha"]))
        dxy = await _precios.instantanea(["DXY"])
        d = dxy.get("DXY")
        if d and d.get("var_1d") is not None:
            sube = d["var_1d"] > 0
            motores.append({"nombre": "Dólar (DXY)", "valor": round(d["ultimo"], 2), "unidad": "",
                "delta": round(d["var_1d"], 2), "direccion": "sube" if sube else "baja",
                "lectura": "desfavorable: dólar fuerte pesa sobre la plata" if sube
                           else "favorable: dólar débil impulsa la plata", "fecha": "en vivo"})
        # matiz propio de la plata: componente industrial (a diferencia del oro)
        cobre = await _precios.instantanea(["Cobre"])
        cu = cobre.get("Cobre")
        if cu and cu.get("var_1d") is not None:
            sube = cu["var_1d"] > 0
            motores.append({"nombre": "Cobre (proxy ciclo industrial)", "valor": round(cu["ultimo"], 2),
                "unidad": "", "delta": round(cu["var_1d"], 2), "direccion": "sube" if sube else "baja",
                "lectura": "favorable: demanda industrial fuerte (la plata es medio industrial)" if sube
                           else "desfavorable: se enfría la demanda industrial", "fecha": "en vivo"})
        # ratio oro/plata: caro/barato relativo
        gp = await _precios.instantanea(["Oro", "Plata"])
        oro_p = gp.get("Oro", {}).get("ultimo"); plata_p = gp.get("Plata", {}).get("ultimo")
        if oro_p and plata_p:
            ratio = round(oro_p / plata_p, 1)
            motores.append({"nombre": "Ratio oro/plata", "valor": ratio, "unidad": "",
                "delta": None, "direccion": "neutral",
                "lectura": f"histórico ~60-80. Ratio alto ({ratio}) = plata barata vs oro; "
                           f"ratio bajo = plata cara vs oro", "fecha": "en vivo"})

    elif clave == "petroleo":
        import eia as _eia
        ed = await _eia.datos()
        inv = ed.get("Inventarios crudo")
        if inv:
            motores.append(_motor("Inventarios de crudo", inv["valor"], "kb", inv["previo"],
                "bajista: acumulación, más oferta disponible",
                "alcista: reducción de existencias, tensiona la oferta", inv["fecha"]))
        gas = ed.get("Inventarios gasolina")
        if gas:
            motores.append(_motor("Inventarios gasolina", gas["valor"], "kb", gas["previo"],
                "bajista: menos demanda de producto",
                "alcista: demanda de producto firme", gas["fecha"]))
        prod = ed.get("Producción crudo EEUU")
        if prod:
            motores.append(_motor("Producción EEUU", prod["valor"], "kb/d", prod["previo"],
                "bajista: más oferta doméstica",
                "alcista: menos oferta doméstica", prod["fecha"]))
        cot_wti = await _cot.historico("WTI")
        if cot_wti:
            u = cot_wti[-1]; p = cot_wti[-2]["neto"] if len(cot_wti) > 1 else None
            motores.append(_motor("COT fondos gestionados", u["neto"], "", p,
                "más largos: apetito alcista",
                "recortan largos: se enfría el posicionamiento", u["fecha"]))

    elif clave == "forex":
        fd = await _fred.datos()
        oat = fd.get("Spread OAT-Bund")
        if oat:
            alerta = oat["valor"] >= 80
            motores.append({"nombre": "Spread OAT-Bund", "valor": oat["valor"], "unidad": "pb",
                "delta": None, "direccion": "alerta" if alerta else "plano",
                "lectura": "ALERTA: fragmentación soberana, presión sobre el euro" if alerta
                           else "riesgo soberano contenido", "fecha": oat["fecha"]})
        for etiqueta, contrato in [("Yen (6J vs USD)", "Yen (6J vs USD)"),
                                    ("Euro (6E vs USD)", "Euro (6E vs USD)")]:
            serie = await _cot.historico(contrato)
            if serie:
                u = serie[-1]; p = serie[-2]["neto"] if len(serie) > 1 else None
                divisa = "yen" if "Yen" in etiqueta else "euro"
                motores.append(_motor(f"COT {divisa}", u["neto"], "", p,
                    f"fondos recomprando {divisa}: gira el par",
                    f"fondos cortos de {divisa}: presión bajista sobre la divisa", u["fecha"]))
        dxy = await _precios.instantanea(["DXY"])
        d = dxy.get("DXY")
        if d and d.get("var_1d") is not None:
            sube = d["var_1d"] > 0
            motores.append({"nombre": "Dólar (DXY)", "valor": round(d["ultimo"], 2), "unidad": "",
                "delta": round(d["var_1d"], 2), "direccion": "sube" if sube else "baja",
                "lectura": "dólar fuerte: presiona a EUR/USD y sostiene USD/JPY" if sube
                           else "dólar débil: alivia a los pares contra el dólar", "fecha": "en vivo"})

    elif clave == "macro":
        fd = await _fred.datos()
        tr = fd.get("Tipo real 10A (EEUU)")
        if tr:
            motores.append(_motor("Tipo real 10A", tr["valor"], "%", tr["previo"],
                "endurecimiento: presiona activos de riesgo",
                "relajación: alivia activos de riesgo", tr["fecha"]))
        bk = fd.get("Breakeven inflación 10A")
        if bk:
            motores.append(_motor("Breakeven inflación", bk["valor"], "%", bk["previo"],
                "más expectativa de inflación", "menos expectativa de inflación", bk["fecha"]))
        for simbolo, nombre, sube_txt, baja_txt in [
            ("VIX", "VIX (miedo)", "riesgo al alza: aversión", "riesgo a la baja: apetito"),
            ("US 10A", "Bono 10A EEUU", "tipos al alza", "tipos a la baja")]:
            d = (await _precios.instantanea([simbolo])).get(simbolo)
            if d and d.get("var_1d") is not None:
                sube = d["var_1d"] > 0
                motores.append({"nombre": nombre, "valor": round(d["ultimo"], 2), "unidad": "",
                    "delta": round(d["var_1d"], 2), "direccion": "sube" if sube else "baja",
                    "lectura": sube_txt if sube else baja_txt, "fecha": "en vivo"})

    elif clave == "cripto":
        # Correlación con el Nasdaq: BTC se comporta como activo de riesgo tecnológico
        ndq = (await _precios.instantanea(["Nasdaq"])).get("Nasdaq")
        if ndq and ndq.get("var_1d") is not None:
            sube = ndq["var_1d"] > 0
            motores.append({"nombre": "Nasdaq (riesgo)", "valor": round(ndq["ultimo"], 0), "unidad": "",
                "delta": round(ndq["var_1d"], 2), "direccion": "sube" if sube else "baja",
                "lectura": "favorable: apetito de riesgo tecnológico arrastra a cripto" if sube
                           else "desfavorable: aversión al riesgo pesa sobre cripto", "fecha": "en vivo"})
        # DXY: dólar fuerte drena liquidez de los activos de riesgo
        dxy = (await _precios.instantanea(["DXY"])).get("DXY")
        if dxy and dxy.get("var_1d") is not None:
            sube = dxy["var_1d"] > 0
            motores.append({"nombre": "Dólar (DXY)", "valor": round(dxy["ultimo"], 2), "unidad": "",
                "delta": round(dxy["var_1d"], 2), "direccion": "sube" if sube else "baja",
                "lectura": "desfavorable: dólar fuerte drena liquidez de cripto" if sube
                           else "favorable: dólar débil libera liquidez hacia cripto", "fecha": "en vivo"})
        # COT de futuros de Bitcoin (CME)
        cot_btc = await _cot.historico("Bitcoin (CME)")
        if cot_btc:
            u = cot_btc[-1]; p = cot_btc[-2]["neto"] if len(cot_btc) > 1 else None
            motores.append(_motor("COT Bitcoin (CME)", u["neto"], "", p,
                "fondos apalancados más largos: apetito institucional",
                "fondos recortando: se enfría el posicionamiento", u["fecha"]))
        # Métricas on-chain: salud y uso de la red
        import onchain as _onchain
        od = await _onchain.datos()
        hr = od.get("Hashrate")
        if hr:
            motores.append(_motor("Hashrate", hr["valor"], "EH/s", hr["previo"],
                "seguridad de red al alza: mineros comprometidos",
                "hashrate cayendo: mineros capitulando, señal de tensión"))
        act = od.get("Direcciones activas")
        if act:
            motores.append(_motor("Direcciones activas", act["valor"], "/día", act["previo"],
                "más uso de la red: demanda real creciente",
                "menos actividad: enfriamiento del uso"))
        # Derivados de Binance: funding, open interest, long/short
        import derivados as _deriv
        dd = await _deriv.datos()
        fr = dd.get("Funding rate")
        if fr:
            pos = fr["valor"] > 0.01  # funding notablemente positivo
            neg = fr["valor"] < -0.005
            if pos:
                motores.append({"nombre": "Funding rate", "valor": f"{fr['valor']:+.4f}", "unidad": "%/8h",
                    "delta": None, "direccion": "sube",
                    "lectura": "desfavorable: largos apalancados sobrecalentados, riesgo de purga",
                    "fecha": "en vivo"})
            elif neg:
                motores.append({"nombre": "Funding rate", "valor": f"{fr['valor']:+.4f}", "unidad": "%/8h",
                    "delta": None, "direccion": "baja",
                    "lectura": "favorable: cortos hacinados, posible squeeze al alza",
                    "fecha": "en vivo"})
            else:
                motores.append({"nombre": "Funding rate", "valor": f"{fr['valor']:+.4f}", "unidad": "%/8h",
                    "delta": None, "direccion": "plano",
                    "lectura": "funding equilibrado, sin tensión de apalancamiento", "fecha": "en vivo"})
        oi = dd.get("Open interest")
        if oi:
            motores.append(_motor("Open interest", oi["valor"], "BTC", oi["previo"],
                "más capital apalancado: convicción, pero más combustible para liquidaciones",
                "se reduce el apalancamiento: menos riesgo de cascada"))
        lg = dd.get("Long/short global")
        if lg:
            extremo_largo = lg["largos"] > 60
            motores.append({"nombre": "Long/short retail", "valor": f"{lg['valor']:.2f}", "unidad": "",
                "delta": None, "direccion": "sube" if extremo_largo else "plano",
                "lectura": "desfavorable: retail demasiado largo (contrarian bajista)" if extremo_largo
                           else "posicionamiento retail equilibrado", "fecha": "en vivo"})

    # Sesgo mecánico: suma las direcciones de los motores que apuntan a favor/en contra del activo.
    # Cada motor lleva su lectura direccional; deducimos el signo de su texto ("favorable"/"alcista"
    # → +1; "desfavorable"/"bajista" → -1; alerta cuenta como riesgo, no como dirección de precio).
    votos = 0
    contados = 0
    NEG = ("desfavorable", "bajista", "pesa sobre", "presiona", "más oferta",
           "enfría", "se enfría", "bajista:", "sube el coste",
           "drena liquidez", "capitulando", "capitulación", "menos actividad", "menos uso",
           "riesgo de purga", "contrarian bajista", "combustible para liquidaciones")
    POS = ("favorable", "alcista", "impulsa", "empuja", "refugio",
           "tensiona la oferta", "baja el coste",
           "libera liquidez", "comprometidos", "seguridad de red al alza",
           "más uso", "demanda real creciente", "apetito institucional",
           "squeeze al alza", "menos riesgo de cascada")
    for m in motores:
        lec = (m.get("lectura") or "").lower()
        if m.get("direccion") == "alerta":
            continue
        # comprobar NEGATIVO primero: "desfavorable" contiene "favorable" como subcadena
        if any(t in lec for t in NEG):
            votos -= 1; contados += 1
        elif any(t in lec for t in POS):
            votos += 1; contados += 1

    if contados == 0:
        sesgo, sesgo_txt = "neutral", "sin señal direccional clara de los motores"
    elif votos >= 2:
        sesgo, sesgo_txt = "alcista", "los motores apuntan mayoritariamente a favor del activo"
    elif votos <= -2:
        sesgo, sesgo_txt = "bajista", "los motores apuntan mayoritariamente en contra del activo"
    elif votos == 1:
        sesgo, sesgo_txt = "alcista-leve", "ligero sesgo a favor, motores mixtos"
    elif votos == -1:
        sesgo, sesgo_txt = "bajista-leve", "ligero sesgo en contra, motores mixtos"
    else:
        sesgo, sesgo_txt = "neutral", "motores equilibrados, sin dirección dominante"

    # Lectura del agente: del hilo de mayor relevancia de este mercado, si lo hay
    lectura_agente = None
    claves_hilo = _MERCADOS.get(clave, [])
    hilos_mercado = [
        h for h in memoria.listar_hilos()
        if any(k in h["nombre"].lower() for k in claves_hilo)
    ]
    if hilos_mercado:
        top = max(hilos_mercado, key=lambda h: h["relevancia"])
        estado = top.get("estado") or top.get("estado_resumen") or ""
        # primera frase o dos del estado, como lectura cualitativa
        if estado:
            frag = estado.strip().split("\n")[0][:280]
            lectura_agente = {"hilo": top["nombre"], "texto": frag, "relevancia": top["relevancia"]}

    return {"clave": clave, "motores": motores,
            "sesgo": sesgo, "sesgo_texto": sesgo_txt,
            "n_motores": contados, "lectura_agente": lectura_agente}


@app.get("/calibracion")
async def pagina_calibracion():
    return FileResponse("static/calibracion.html")


@app.get("/mercados")
async def pagina_mercados():
    return FileResponse("static/mercados.html")


@app.get("/api/calibracion")
async def api_calibracion(schema_version: int | None = None, resuelto_por: str | None = None,
                          specification_source: str | None = None):
    return memoria.calibracion_detalle(schema_version=schema_version, resuelto_por=resuelto_por,
                                      specification_source=specification_source)


@app.get("/api/predicciones")
async def api_predicciones(schema_version: int | None = None, resuelto_por: str | None = None,
                           specification_source: str | None = None):
    filtros = dict(schema_version=schema_version, resuelto_por=resuelto_por,
                   specification_source=specification_source)
    return {
        "abiertas": memoria.predicciones_abiertas(),
        "resueltas": memoria.predicciones_resueltas(**filtros),
        "calibracion": memoria.calibracion(**filtros),
    }


@app.get("/api/predicciones/todas")
async def api_predicciones_todas():
    """Todas las predicciones del modelo agrupadas por instrumento/tema."""
    return await asyncio.to_thread(memoria.todas_las_predicciones)


@app.get("/api/predicciones/recap")
async def api_predicciones_recap():
    """Predicciones que vencen en los próximos 7 días (recap semanal)."""
    return await asyncio.to_thread(memoria.recap_semanal)


@app.get("/api/biblioteca/consultas")
async def api_biblioteca_consultas():
    """Historial de consultas del agente a la biblioteca — para auditar qué libros usa."""
    import sqlite3
    def _leer():
        try:
            c = sqlite3.connect("market_pulse.db")
            c.row_factory = sqlite3.Row
            filas = [dict(f) for f in c.execute(
                "SELECT * FROM consultas_biblioteca ORDER BY momento DESC LIMIT 100").fetchall()]
            # resumen por libro
            resumen = {}
            for f in filas:
                lt = f["libro_top"] or f["libro_pedido"] or "(sin resultado)"
                resumen[lt] = resumen.get(lt, 0) + 1
            c.close()
            return {"total": len(filas), "por_libro": resumen, "consultas": filas[:40]}
        except Exception:
            return {"total": 0, "por_libro": {}, "consultas": [], "aviso": "aún no hay consultas registradas"}
    return await asyncio.to_thread(_leer)


@app.get("/api/predicciones/revision")
async def api_revision():
    return await asyncio.to_thread(validation.cola_revision)


@app.get("/api/predicciones/{prediccion_id}/auditoria")
async def api_auditoria(prediccion_id: int):
    with db._conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM validacion_auditoria WHERE prediccion_id=? ORDER BY id", (prediccion_id,))]


@app.post("/api/predicciones/{prediccion_id}/resolver")
async def api_resolver(prediccion_id: int, payload: dict):
    if "resultado" not in payload:
        raise HTTPException(422, "resultado obligatorio: 1 ocurrió, 0 no ocurrió, NULL no resoluble")
    result = await asyncio.to_thread(validation.resolver_manual, prediccion_id, payload["resultado"],
                                    payload.get("fuente_resolucion", payload.get("nota", "")),
                                    payload.get("fecha_resolucion"))
    if not result["ok"]:
        raise HTTPException(409 if "ya resuelta" in result["error"] else 422, result["error"])
    return result


@app.post("/api/informe/{tipo}")
async def api_informe(tipo: str):
    if tipo not in ("overnight", "cierre", "ahora"):
        return {"ok": False, "motivo": "tipo debe ser overnight, cierre o ahora"}
    asyncio.create_task(informe(tipo, origen="manual"))
    return {"ok": True, "aviso": "generando; revisa ultimo_informe / ultimo_error en /api/estado"}


@app.get("/api/consumo-ia")
async def api_consumo_ia(horas: int = 24, dia: str | None = None):
    try:
        if dia is not None:
            date.fromisoformat(dia)
        return await asyncio.to_thread(consumo_ia.resumen, horas=horas, dia=dia)
    except ValueError as exc:
        raise HTTPException(422, "Usa horas=24/48 o dia=YYYY-MM-DD") from exc
