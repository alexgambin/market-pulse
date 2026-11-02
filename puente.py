"""
puente.py — Puente fundamentals entre Market Pulse y Polymarket (fase B).

Conecta los dos mundos: cuando un mercado de Polymarket pregunta por un nivel de
precio de un activo que Market Pulse cubre (BTC, oro, WTI, etc.), este módulo calcula
la probabilidad FUNDAMENTADA de Market Pulse y la compara con el precio de mercado
(que es la probabilidad implícita del mercado). Donde divergen ≥ umbral, hay un
"edge fundamental" — distinto del Optimism Tax de Becker.

MODO SOLO LECTURA: este puente NO toca el sizing ni las decisiones del motor de
paper trading. Solo calcula, muestra y REGISTRA la señal, para que dentro de unos
meses se pueda medir con datos limpios si las señales de Market Pulse anticipaban
el resultado. Los dos relojes (validar EDGE / validar Market Pulse) corren juntos.

Fuentes de probabilidad, en orden:
  1. tasa_base (frecuencia histórica objetiva, siempre disponible, sin sesgo)
  2. predicción del agente sobre ese mismo nivel, si existe (más rica, pero se
     corrige por el sesgo de calibración conocido antes de usarla)

Autotest:  python puente.py
"""
import re
import os
import sqlite3
from datetime import datetime, timezone

import tasabase
import precios

DB_PATH = os.environ.get("MP_DB_PATH", "market_pulse.db")

# Umbral de divergencia para marcar "edge fundamental" (puntos porcentuales)
UMBRAL_EDGE_PP = 10

# ── Eventos macro de bancos centrales (no son niveles de precio, son decisiones) ──
# Estos mercados no se evalúan con tasa_base (frecuencia de movimiento de precio),
# sino con la predicción de EVENTO del agente, que razona con los libros de macro.
BANCOS_CENTRALES = {
    "Fed": ["fed", "fomc", "federal reserve", "powell", "jerome powell"],
    "BCE": ["ecb", "european central bank", "lagarde", "bce"],
    "BoE": ["bank of england", "boe", "bailey"],
    "BoJ": ["bank of japan", "boj", "ueda"],
}
_PALABRAS_TIPOS = ["rate", "rates", "hike", "cut", "basis point", "bps", "bp ",
                   "raise", "lower", "hold", "pause", "tipos", "interest rate"]


def _detectar_evento_tipos(pregunta: str):
    """Si el mercado es sobre una decisión de tipos de un banco central, devuelve el banco."""
    q = (pregunta or "").lower()
    if not any(p in q for p in _PALABRAS_TIPOS):
        return None
    for banco, alias in BANCOS_CENTRALES.items():
        if any(a in q for a in alias):
            return banco
    return None


def _prediccion_evento_agente(banco: str, pregunta: str):
    """Busca una predicción de tipo 'evento' del agente relacionada con este banco central."""
    try:
        with sqlite3.connect(DB_PATH) as c:
            c.row_factory = sqlite3.Row
            filas = c.execute(
                "SELECT probabilidad, afirmacion FROM predicciones "
                "WHERE tipo='evento' AND resuelto IS NULL").fetchall()
    except Exception:
        return None
    alias = [a for a in BANCOS_CENTRALES.get(banco, [])]
    # buscar una predicción cuya afirmación mencione el banco y hable de tipos
    for f in filas:
        af = (f["afirmacion"] or "").lower()
        if any(a in af for a in alias) and any(p in af for p in _PALABRAS_TIPOS):
            return {
                "prob_cruda": f["probabilidad"],
                "prob_corregida": round(_correccion_sesgo(f["probabilidad"]), 3),
                "afirmacion": f["afirmacion"],
            }
    return None


# ── Reconocimiento de activo en la pregunta de Polymarket ──
# Cada activo con sus alias; se mapea a la clave interna de precios.INSTRUMENTOS.
ALIAS_ACTIVO = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "ether", "eth"],
    "Oro": ["gold", "xau"],
    "WTI": ["oil", "crude", "wti", "petroleum"],
    "Brent": ["brent"],
    "S&P 500": ["s&p", "sp500", "s&p 500"],
    "Nasdaq": ["nasdaq"],
    "Russell 2000": ["russell", "russell 2000", "rut"],
    "Dow (US30)": ["dow jones", "dow", "us30", "djia"],
    "Plata": ["silver", "plata", "xag"],
    "Cobre": ["copper", "cobre"],
    "Platino": ["platinum", "platino"],
    "EUR/USD": ["eur/usd", "euro"],
    "USD/JPY": ["usd/jpy", "yen"],
    "Gas nat.": ["natural gas", "natgas"],
}


def _detectar_activo(pregunta: str):
    """Devuelve la clave interna del activo (BTC, Oro…) o None."""
    q = (pregunta or "").lower()
    for clave, alias in ALIAS_ACTIVO.items():
        if any(a in q for a in alias):
            # solo si tenemos ese instrumento en yfinance
            if clave in precios.INSTRUMENTOS:
                return clave
    return None


def _detectar_nivel(pregunta: str):
    """Extrae el nivel de precio objetivo de la pregunta (ej. '$100k' → 100000)."""
    q = pregunta.replace(",", "")
    # patrón con k/K (miles): $100k, 100k
    m = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*[kK]\b", q)
    if m:
        return float(m.group(1)) * 1000
    # patrón con dólar explícito: $100000, $85.50
    m = re.search(r"\$\s*(\d+(?:\.\d+)?)", q)
    if m:
        return float(m.group(1))
    # número suelto grande (para cripto) o con decimales (para FX/materias)
    m = re.search(r"\b(\d{2,}(?:\.\d+)?)\b", q)
    if m:
        return float(m.group(1))
    return None


def _detectar_direccion(pregunta: str):
    """¿El mercado pregunta si SUBE por encima o BAJA por debajo del nivel?"""
    q = (pregunta or "").lower()
    if any(w in q for w in ["above", "over", "reach", "exceed", "hit", "higher", "surpass", ">"]):
        return "sube"
    if any(w in q for w in ["below", "under", "drop", "fall", "lower", "less than", "<"]):
        return "baja"
    return "sube"  # por defecto los mercados suelen preguntar "¿llegará a X?"


def _dias_hasta(fecha_iso: str):
    """Días desde hoy hasta la fecha de resolución del mercado."""
    if not fecha_iso:
        return None
    try:
        end = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
        dias = (end - datetime.now(timezone.utc)).total_seconds() / 86400
        return max(1, round(dias))
    except (ValueError, AttributeError):
        return None


def _ventana_para(dias: int) -> str:
    """Elige la ventana de tasa_base más cercana al horizonte del mercado.

    tasa_base tiene ventanas discretas (1d,3d,5d,10d,20d). Para horizontes largos
    usamos la mayor disponible (20d) — es una aproximación conservadora: la
    frecuencia de tocar un nivel en 20 sesiones es una cota inferior de tocarlo
    en, p.ej., 90 días. El puente lo marca como aproximado.
    """
    if dias <= 1:
        return "1d"
    if dias <= 3:
        return "3d"
    if dias <= 7:
        return "5d"
    if dias <= 15:
        return "10d"
    return "20d"


# ── Fuente 2: predicción del agente sobre ese nivel, si existe ──
# Se corrige por el sesgo de calibración conocido antes de usarla.
def _correccion_sesgo(prob: float) -> float:
    """Corrige la probabilidad del agente por su sesgo de calibración conocido.

    De la auditoría: el agente infla la banda intermedia. 60-80% declara ~0.66 y
    ocurre ~0.40; 40-60% declara ~0.49 y ocurre ~0.25. Regla aplicada: por debajo
    de 0.60, divide por ~2; 0.60-0.80 se recorta ~0.26; >0.80 casi tal cual.
    """
    if prob < 0.40:
        return prob * 0.9
    if prob < 0.60:
        return prob * 0.55
    if prob < 0.80:
        return max(0.40, prob - 0.26)
    return prob


def _prediccion_agente(activo: str, nivel: float, direccion: str):
    """Busca una predicción abierta del agente sobre este activo y nivel similar."""
    try:
        with sqlite3.connect(DB_PATH) as c:
            c.row_factory = sqlite3.Row
            filas = c.execute(
                "SELECT probabilidad, nivel_objetivo, afirmacion FROM predicciones "
                "WHERE instrumento=? AND resuelto IS NULL AND nivel_objetivo IS NOT NULL",
                (activo,),
            ).fetchall()
    except Exception:
        return None
    # buscar la predicción cuyo nivel objetivo esté más cerca (dentro del 3%)
    mejor = None
    for f in filas:
        obj = f["nivel_objetivo"]
        if obj and abs(obj / nivel - 1) < 0.03:
            mejor = f
            break
    if not mejor:
        return None
    return {
        "prob_cruda": mejor["probabilidad"],
        "prob_corregida": round(_correccion_sesgo(mejor["probabilidad"]), 3),
        "afirmacion": mejor["afirmacion"],
    }


async def evaluar(pregunta: str, precio_yes: int, fecha_iso: str = None):
    """Evalúa un mercado de Polymarket contra Market Pulse. Devuelve la señal o None.

    precio_yes: precio YES en centavos (= probabilidad implícita del mercado en %).
    """
    # Rama 1: ¿es una decisión de tipos de un banco central? (evento, no nivel de precio)
    banco = _detectar_evento_tipos(pregunta)
    if banco:
        pred = _prediccion_evento_agente(banco, pregunta)
        if not pred:
            # el agente aún no ha opinado sobre esta decisión → sin señal (pero puede crearla)
            return {
                "tipo": "evento_tipos", "banco": banco,
                "prob_fundamentada": None, "prob_mercado": round(precio_yes / 100, 3),
                "divergencia_pp": None, "señal": "sin predicción del agente todavía",
                "fuente": "—", "activo": banco,
                "nota": f"Mercado de tipos del {banco}. El agente aún no ha registrado una "
                        f"predicción de evento sobre esta decisión; la creará al analizar el calendario.",
            }
        prob_fund = pred["prob_corregida"]
        prob_mercado = precio_yes / 100
        divergencia = (prob_fund - prob_mercado) * 100
        señal = "neutral"
        if divergencia >= UMBRAL_EDGE_PP:
            señal = f"MP ve la decisión del {banco} más probable que el mercado"
        elif divergencia <= -UMBRAL_EDGE_PP:
            señal = f"MP ve la decisión del {banco} menos probable que el mercado"
        return {
            "tipo": "evento_tipos", "banco": banco, "activo": banco,
            "prob_fundamentada": round(prob_fund, 3),
            "prob_mercado": round(prob_mercado, 3),
            "divergencia_pp": round(divergencia, 1),
            "señal": señal, "fuente": "agente (evento, corregido)",
            "afirmacion_agente": pred["afirmacion"],
        }

    # Rama 2: nivel de precio de un activo que Market Pulse cubre
    activo = _detectar_activo(pregunta)
    if not activo:
        return None  # Market Pulse no cubre este activo → sin señal fundamental
    nivel = _detectar_nivel(pregunta)
    if not nivel:
        return None
    direccion = _detectar_direccion(pregunta)
    dias = _dias_hasta(fecha_iso)
    ventana = _ventana_para(dias) if dias else "20d"

    # precio actual del activo
    inst = await precios.instantanea([activo])
    actual = inst.get(activo, {}).get("ultimo")
    if not actual:
        return None

    # % de movimiento necesario para alcanzar el nivel desde el precio actual
    mov_pct = (nivel / actual - 1) * 100
    # si el nivel ya está superado en la dirección preguntada, prob alta trivial
    if direccion == "sube" and mov_pct <= 0:
        prob_tb = 0.90
        clase = f"{activo} ya sobre {nivel:g} (actual {actual:g})"
    elif direccion == "baja" and mov_pct >= 0:
        prob_tb = 0.90
        clase = f"{activo} ya bajo {nivel:g} (actual {actual:g})"
    else:
        # frecuencia histórica de un movimiento de esa magnitud en el horizonte EXACTO
        # del mercado (en días naturales), no una ventana aproximada
        if dias:
            r = await tasabase.tasa_base_dias(activo, direccion, abs(mov_pct), dias, anios=5)
        else:
            r = await tasabase.tasa_base(activo, direccion, abs(mov_pct), "20d", anios=3)
        if "error" in r:
            return None
        prob_tb = r["frecuencia"]
        clase = r["clase"]

    # fuente 2: predicción del agente si existe (corregida por sesgo)
    pred = _prediccion_agente(activo, nivel, direccion)

    # probabilidad fundamentada final: tasa_base de base, agente si existe
    prob_fund = pred["prob_corregida"] if pred else prob_tb
    fuente = "agente (corregido) + tasa_base" if pred else "tasa_base"

    prob_mercado = precio_yes / 100
    divergencia = (prob_fund - prob_mercado) * 100  # en pp

    señal = "neutral"
    if divergencia >= UMBRAL_EDGE_PP:
        señal = "MP más alcista que el mercado"  # MP ve más probable el SÍ
    elif divergencia <= -UMBRAL_EDGE_PP:
        señal = "MP más bajista que el mercado"  # MP ve menos probable el SÍ

    return {
        "activo": activo,
        "nivel": nivel,
        "direccion": direccion,
        "precio_actual": round(actual, 2),
        "mov_necesario_pct": round(mov_pct, 1),
        "ventana": ventana,
        "dias_horizonte": dias,
        "prob_fundamentada": round(prob_fund, 3),
        "prob_mercado": round(prob_mercado, 3),
        "divergencia_pp": round(divergencia, 1),
        "señal": señal,
        "fuente": fuente,
        "clase_tasa_base": clase,
        "tiene_prediccion_agente": pred is not None,
        "horizonte_exacto": dias is not None,  # True = ventana calculada para los días reales
    }


# ── Registro para medición posterior (solo lectura) ──
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS pm_senales_fund (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT,
                market_id TEXT,
                activo TEXT,
                nivel REAL,
                direccion TEXT,
                prob_fundamentada REAL,
                prob_mercado REAL,
                divergencia_pp REAL,
                senal TEXT,
                fuente TEXT,
                registrado TEXT,
                resuelto_como INTEGER
            )
        """)


def registrar_senal(trade_id, market_id, señal: dict):
    """Guarda la señal fundamental junto a un trade, para medir después. Solo lectura:
    no influye en el trade, solo se anota para la validación de dentro de unos meses."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.execute("BEGIN IMMEDIATE")
        # La consulta de precios puede haber terminado después de un reset.
        # Comprobar e insertar juntos evita señales huérfanas o repetidas.
        if not c.execute("SELECT 1 FROM pm_trades WHERE id=?", (trade_id,)).fetchone():
            return
        if c.execute("SELECT 1 FROM pm_senales_fund WHERE trade_id=?", (trade_id,)).fetchone():
            return
        c.execute(
            "INSERT INTO pm_senales_fund (trade_id, market_id, activo, nivel, direccion, "
            "prob_fundamentada, prob_mercado, divergencia_pp, senal, fuente, registrado, resuelto_como) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, market_id, señal["activo"], señal["nivel"], señal["direccion"],
             señal["prob_fundamentada"], señal["prob_mercado"], señal["divergencia_pp"],
             señal["señal"], señal["fuente"],
             datetime.now(timezone.utc).isoformat(timespec="seconds"), None),
        )


def resumen_senales():
    """Estado de las señales fundamentales registradas, para el dashboard."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        filas = [dict(f) for f in c.execute(
            "SELECT * FROM pm_senales_fund ORDER BY registrado DESC LIMIT 100").fetchall()]
    con_edge = [f for f in filas if f["senal"] != "neutral"]
    return {
        "total": len(filas),
        "con_edge_fundamental": len(con_edge),
        "senales": filas[:50],
    }


if __name__ == "__main__":
    import asyncio

    async def _t():
        print("Probando el puente fundamentals (modo solo lectura)…\n")
        casos = [
            ("Will Bitcoin reach $100k by December 31, 2026?", 55, "2026-12-31T00:00:00Z"),
            ("Will Bitcoin close above $70k by September 30?", 60, "2026-09-30T00:00:00Z"),
            ("Will oil (WTI) close above $90 before year end?", 40, "2026-12-31T00:00:00Z"),
            ("Will gold reach $4500 by October?", 50, "2026-10-31T00:00:00Z"),
            ("Iran ceasefire before June?", 30, "2026-06-01T00:00:00Z"),  # sin activo → None
        ]
        for pregunta, precio_yes, fecha in casos:
            r = await evaluar(pregunta, precio_yes, fecha)
            if not r:
                print(f"  '{pregunta[:50]}' → sin señal (Market Pulse no cubre este activo)")
                continue
            print(f"  '{pregunta[:50]}'")
            print(f"     {r['activo']} nivel {r['nivel']:g} · precio actual {r['precio_actual']:g} "
                  f"· mueve {r['mov_necesario_pct']:+.1f}% en {r['dias_horizonte']}d"
                  + (" (horizonte exacto)" if r["horizonte_exacto"] else " (aprox)"))
            print(f"     MP fundamentada {r['prob_fundamentada']:.0%} vs mercado {r['prob_mercado']:.0%} "
                  f"→ divergencia {r['divergencia_pp']:+.0f}pp → {r['señal']}")
            print(f"     fuente: {r['fuente']}\n")

    asyncio.run(_t())
