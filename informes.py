"""
informes.py — Paso 3: informes programados (overnight 07:00, cierre 22:00) por Telegram.

Necesita dos variables de entorno además de ANTHROPIC_API_KEY:
    TELEGRAM_BOT_TOKEN   (lo da @BotFather al crear el bot)
    TELEGRAM_CHAT_ID     (tu id de chat con el bot)

El informe se genera con una sola llamada a Sonnet a partir de la memoria de
hilos + las últimas síntesis + titulares recientes, se envía por Telegram y se
archiva en la tabla de análisis (así aparece también en el panel del dashboard).
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import httpx

import calendario
import cot
import db
import config
import consumo_ia
import eia
import fred
import memoria
import precios
from agente import MODELO_AGENTE, cargar_doctrina

ZONA_LOCAL = ZoneInfo("Europe/Madrid")
LIMITE_TELEGRAM = 3900  # margen bajo el máximo real de 4096 caracteres

SISTEMAS = {
    "overnight": (
        "Eres el analista macro personal de un trader discrecional cuyos instrumentos "
        "son WTI, USD/JPY, oro y EUR/USD. "
        "Escribe su INFORME DE APERTURA de las 07:00 (hora de Madrid). Estructura: "
        "1) QUÉ HA PASADO — lo relevante desde el cierre de ayer (sesión asiática, "
        "titulares nocturnos, cambios en los hilos). 2) ESTADO DE LOS HILOS — solo los "
        "que se movieron o importan hoy, con niveles y fechas concretos. 3) QUÉ VIGILAR "
        "HOY — datos, eventos y niveles. 4) FUERA DEL RADAR — 3 a 5 líneas de lo relevante que NO pertenece al tema dominante (otros mercados, regiones, sectores). Texto plano sin markdown (ni asteriscos ni "
        "almohadillas), secciones en mayúsculas, frases cortas, máximo 3.000 caracteres. "
        "Reglas de conocimiento: tu conocimiento general del mundo (mecánica de mercados, historia, instituciones) es base válida y debes usarlo con naturalidad. Pero para hechos de estado ACTUAL —quién ocupa un cargo, niveles vigentes, fechas recientes— mandan los hilos y titulares del contexto sobre tu memoria de entrenamiento, que puede estar desfasada. Nunca importes nombres propios de episodios históricos parecidos: verifica cada nombre contra el contexto. "
        "Concreto y honesto con la incertidumbre; nada de relleno."
    ),
    "ahora": (
        "Eres el analista macro personal de un trader discrecional cuyos instrumentos "
        "son WTI, USD/JPY, oro y EUR/USD. "
        "Escribe un UPDATE DE MERCADO bajo demanda: la foto de este preciso momento. "
        "Estructura: 1) AHORA MISMO — la cinta y qué la está moviendo. 2) HILOS "
        "CALIENTES — solo los 3-5 que importan en este instante, con niveles y el "
        "porqué. 3) PRÓXIMAS HORAS — qué vigilar y qué cambiaría la lectura. Texto "
        "plano sin markdown (ni asteriscos ni almohadillas), secciones en mayúsculas, "
        "máximo 2.000 caracteres. Concreto y honesto con la incertidumbre; nada de "
        "relleno. "
        "Reglas de conocimiento: tu conocimiento general del mundo (mecánica de "
        "mercados, historia, instituciones) es base válida y debes usarlo con "
        "naturalidad. Pero para hechos de estado ACTUAL —quién ocupa un cargo, niveles "
        "vigentes, fechas recientes— mandan los hilos y titulares del contexto sobre "
        "tu memoria de entrenamiento, que puede estar desfasada. Nunca importes "
        "nombres propios de episodios históricos parecidos: verifica cada nombre "
        "contra el contexto."
    ),
    "cierre": (
        "Eres el analista macro personal de un trader discrecional cuyos instrumentos "
        "son WTI, USD/JPY, oro y EUR/USD. "
        "Escribe su INFORME DE CIERRE de las 22:00 (hora de Madrid). Estructura: "
        "1) BALANCE DEL DÍA — qué se movió y por qué. 2) HILOS — qué cambió hoy en las "
        "narrativas. 3) ABIERTO PARA MAÑANA — riesgos, eventos y niveles a vigilar. "
        "4) FUERA DEL RADAR — 3 a 5 líneas de lo relevante que NO pertenece al tema "
        "dominante (otros mercados, regiones, sectores). "
        "Texto plano sin markdown (ni asteriscos ni almohadillas), secciones en "
        "mayúsculas, frases cortas, máximo 3.000 caracteres. Concreto y honesto con la "
        "incertidumbre; nada de relleno. "
        "Reglas de conocimiento: tu conocimiento general del mundo (mecánica de mercados, historia, instituciones) es base válida y debes usarlo con naturalidad. Pero para hechos de estado ACTUAL —quién ocupa un cargo, niveles vigentes, fechas recientes— mandan los hilos y titulares del contexto sobre tu memoria de entrenamiento, que puede estar desfasada. Nunca importes nombres propios de episodios históricos parecidos: verifica cada nombre contra el contexto. "
        
    ),
}


def _contexto() -> str:
    partes = [f"Fecha y hora local: {datetime.now(ZONA_LOCAL).strftime('%A %d/%m/%Y, %H:%M')}"]

    hilos = memoria.listar_hilos()
    if hilos:
        partes.append("\n== MEMORIA DE HILOS ==")
        for h in hilos[:10]:
            completo = memoria.leer_hilo(h["id"])
            partes.append(
                f"\n-- {h['nombre']} (relevancia {h['relevancia']}, "
                f"actualizado {h['actualizado']})\n{completo.get('estado', '')}"
            )

    analisis = db.ultimos_analisis(3)
    if analisis:
        partes.append("\n== ÚLTIMAS SÍNTESIS DEL AGENTE ==")
        for a in analisis:
            partes.append(f"\n[{a['creado']}]\n{a['texto']}")

    abiertas = memoria.predicciones_abiertas()
    cal = memoria.calibracion()
    if abiertas or cal.get("n"):
        partes.append("\n== PREDICCIONES DEL AGENTE ==")
        for p in abiertas:
            partes.append(f"- p={p['probabilidad']:.2f}, vence {p['horizonte']}: {p['afirmacion']}")
        if cal.get("n"):
            partes.append(f"Calibración: {cal['n']} resueltas, Brier medio {cal['brier_medio']}, sesgo {cal['sesgo']:+.3f}")

    noticias = db.ultimas_noticias(15)
    if noticias:
        partes.append("\n== ÚLTIMOS TITULARES ==")
        for n in noticias:
            partes.append(f"- ({n['fuente']}) {n['titular']}")

    return "\n".join(partes)


async def generar_informe(api_key: str, tipo: str, contexto=None) -> str:
    if tipo not in SISTEMAS:
        raise ValueError(f"tipo de informe desconocido: {tipo}")
    cliente = anthropic.AsyncAnthropic(api_key=api_key)
    la_cinta = await precios.cinta()
    posicionamiento = await cot.resumen()
    if posicionamiento:
        la_cinta = la_cinta + "\n\n" + posicionamiento
    horas = {"overnight": 24, "cierre": 36, "ahora": 12}.get(tipo, 24)
    agenda = await calendario.proximos(horas)
    if agenda:
        la_cinta = la_cinta + "\n\n" + agenda
    estructural = await fred.macro()
    if estructural:
        la_cinta = la_cinta + "\n\n" + estructural
    fisica = await eia.energia()
    if fisica:
        la_cinta = la_cinta + "\n\n" + fisica
    contexto = contexto or consumo_ia.Contexto(
        {"overnight": "informe_manana", "cierre": "informe_noche", "ahora": "informe_ahora"}[tipo], tipo)
    r = await consumo_ia.llamar(cliente.messages.create, contexto,
        model=MODELO_AGENTE,
        max_tokens=2000,
        system=SISTEMAS[tipo] + cargar_doctrina(),
        messages=[{"role": "user", "content": la_cinta + "\n\n" + _contexto() + "\n\nEscribe el informe."}],
    )
    return "".join(b.text for b in r.content if b.type == "text").strip()


def _trocear(texto: str) -> list[str]:
    """Telegram corta en 4096 caracteres; troceamos por líneas con margen."""
    if len(texto) <= LIMITE_TELEGRAM:
        return [texto]
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        while len(linea) > LIMITE_TELEGRAM:  # línea monstruosa: corte duro
            if actual.strip():
                trozos.append(actual.rstrip())
                actual = ""
            trozos.append(linea[:LIMITE_TELEGRAM])
            linea = linea[LIMITE_TELEGRAM:]
        if len(actual) + len(linea) + 1 > LIMITE_TELEGRAM:
            trozos.append(actual.rstrip())
            actual = ""
        actual += linea + "\n"
    if actual.strip():
        trozos.append(actual.rstrip())
    return trozos


async def enviar_telegram(texto: str):
    if not config.TELEGRAM_ENABLED:
        consumo_ia.log.info("[TELEGRAM] disabled")
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as web:
        for trozo in _trocear(texto):
            r = await web.post(
                url,
                json={"chat_id": chat_id, "text": trozo, "disable_web_page_preview": True},
            )
            r.raise_for_status()
