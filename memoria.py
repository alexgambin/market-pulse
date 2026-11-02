"""
memoria.py — Memoria persistente del agente (Paso 1) + Capa C (predicciones).

Tablas sobre la misma SQLite del dashboard:
  - hilos:        narrativas vivas del mercado (Hormuz, BoJ, etc.)
  - noticia_hilo: enlaces entre noticias y hilos (el "segundo cerebro")
  - predicciones: registro puntuable — afirmaciones falsables con horizonte,
                  resueltas contra la cinta con puntuación Brier.

Uso:
    from memoria import init_memoria
    init_memoria()   # una vez al arrancar, después de init_db()

Las funciones son síncronas a propósito: son consultas locales de sub-milisegundo
y así el módulo no depende de aiosqlite.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

# Debe apuntar al MISMO archivo que usa tu db.py
DB_PATH = os.environ.get("MP_DB_PATH", "market_pulse.db")


@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        with c:
            yield c
    finally:
        c.close()


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_memoria():
    """Crea las tablas si no existen."""
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS hilos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT UNIQUE NOT NULL,
            estado      TEXT NOT NULL,              -- síntesis viva: qué sabemos, qué cambió, qué vigilar
            relevancia  INTEGER NOT NULL DEFAULT 3, -- 1-5 (5 = crítico para las posiciones)
            creado      TEXT NOT NULL,
            actualizado TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS noticia_hilo (
            noticia_id  INTEGER NOT NULL,           -- id de tu tabla de noticias
            hilo_id     INTEGER NOT NULL REFERENCES hilos(id) ON DELETE CASCADE,
            nota        TEXT,                       -- qué aporta esta noticia al hilo
            creado      TEXT NOT NULL,
            PRIMARY KEY (noticia_id, hilo_id)
        );

        CREATE TABLE IF NOT EXISTS predicciones (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            hilo_id      INTEGER REFERENCES hilos(id) ON DELETE SET NULL,
            afirmacion   TEXT NOT NULL,             -- afirmación falsable
            probabilidad REAL NOT NULL,             -- 0.0 - 1.0
            horizonte    TEXT NOT NULL,             -- p. ej. "2026-08-01" o "FOMC de septiembre"
            creado       TEXT NOT NULL,             -- INMUTABLE: base de la ventana de evaluación
            resuelto     TEXT,                      -- cuándo lo ESCRIBIÓ el sistema
            resultado    INTEGER,                   -- 1 = el evento OCURRIÓ / 0 = no ocurrió / NULL
                                                    -- NO significa "el agente acertó": describe la
                                                    -- realidad, no la calidad del pronóstico. Esa la
                                                    -- mide el Brier. Un 0.2 sobre algo que no ocurre
                                                    -- es un BUEN pronóstico (Brier 0.04, resultado=0).
            brier        REAL,                      -- (probabilidad - resultado)^2
            nota         TEXT,
            tipo         TEXT DEFAULT 'otro',       -- etiqueta del agente; NO gobierna la validación
            instrumento  TEXT,
            nivel_objetivo REAL,
            condicion    TEXT,                      -- '>=', '<=', '>', '<'
            modo         TEXT,                      -- 'toca' | 'cierra' | 'mantiene' (ver config.MODOS)
            fecha_limite TEXT,                      -- fecha inclusiva o timestamp ISO con zona
            fuente_resolucion TEXT,                 -- 'yfinance', 'FRED', 'comunicado_fomc'…
            resuelto_por TEXT,                      -- 'agente' | 'automatico' | 'manual'
            fecha_resolucion TEXT,                  -- cuándo OCURRIÓ el hecho (granularidad de DÍA
                                                    -- con velas diarias). Su diferencia con `resuelto`
                                                    -- es el retardo del resolver.
            provider     TEXT,
            model        TEXT,
            prompt_version TEXT,
            estado_validacion TEXT DEFAULT 'PENDING' -- ver config.ESTADOS_VALIDACION
        );
        """)
        from migrations import migrate_predictions
        migrate_predictions(c)


# ── Herramientas que usará el agente ─────────────────────────────────────────

def listar_hilos():
    """Todos los hilos con un resumen corto del estado (para orientar al agente)."""
    with _conn() as c:
        filas = c.execute(
            "SELECT id, nombre, relevancia, actualizado, estado FROM hilos "
            "ORDER BY relevancia DESC, actualizado DESC"
        ).fetchall()
    return [
        {
            "id": f["id"],
            "nombre": f["nombre"],
            "relevancia": f["relevancia"],
            "actualizado": f["actualizado"],
            "estado_resumen": (f["estado"][:220] + "…") if len(f["estado"]) > 220 else f["estado"],
            "estado": f["estado"],
        }
        for f in filas
    ]


def leer_hilo(hilo_id: int):
    """Estado completo de un hilo y sus últimos enlaces."""
    with _conn() as c:
        h = c.execute("SELECT * FROM hilos WHERE id = ?", (hilo_id,)).fetchone()
        if h is None:
            return {"error": f"no existe el hilo {hilo_id}"}
        enlaces = c.execute(
            "SELECT noticia_id, nota, creado FROM noticia_hilo "
            "WHERE hilo_id = ? ORDER BY creado DESC LIMIT 10",
            (hilo_id,),
        ).fetchall()
    return {**dict(h), "ultimos_enlaces": [dict(e) for e in enlaces]}


def crear_hilo(nombre: str, estado: str, relevancia: int = 3):
    ahora = _ahora()
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO hilos (nombre, estado, relevancia, creado, actualizado) "
                "VALUES (?, ?, ?, ?, ?)",
                (nombre.strip(), estado, int(relevancia), ahora, ahora),
            )
        return {"ok": True, "hilo_id": cur.lastrowid}
    except sqlite3.IntegrityError:
        return {"error": f"ya existe un hilo llamado '{nombre}' — usa actualizar_hilo"}


def actualizar_hilo(hilo_id: int, estado: str, relevancia: int | None = None):
    with _conn() as c:
        if relevancia is None:
            cur = c.execute(
                "UPDATE hilos SET estado = ?, actualizado = ? WHERE id = ?",
                (estado, _ahora(), hilo_id),
            )
        else:
            cur = c.execute(
                "UPDATE hilos SET estado = ?, relevancia = ?, actualizado = ? WHERE id = ?",
                (estado, int(relevancia), _ahora(), hilo_id),
            )
    if cur.rowcount == 0:
        return {"error": f"no existe el hilo {hilo_id}"}
    return {"ok": True}


def enlazar_noticia(noticia_id: int, hilo_id: int, nota: str = ""):
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO noticia_hilo (noticia_id, hilo_id, nota, creado) "
                "VALUES (?, ?, ?, ?)",
                (int(noticia_id), int(hilo_id), nota, _ahora()),
            )
        return {"ok": True}
    except sqlite3.IntegrityError as e:
        return {"error": str(e)}


# ── Capa C: predicciones puntuables ──────────────────────────────────────────

TIPOS_PREDICCION = ("evento", "nivel", "continuidad", "otro")


def registrar_prediccion(afirmacion: str, probabilidad: float, horizonte: str,
                         hilo_id: int | None = None, tipo: str = "otro",
                         instrumento: str | None = None, nivel_objetivo: float | None = None,
                         *, condicion=None, modo=None, fecha_limite=None, provider=None,
                         model=None, model_snapshot=None, prompt_version=None):
    """Registra y congela el contrato original v2 con procedencia contemporánea."""
    import math
    from config import MODOS, OPERADORES, ENGINE_VERSION
    p = float(probabilidad)
    nivel = float(nivel_objetivo) if nivel_objetivo is not None else None
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("probabilidad fuera de [0,1]")
    if nivel is not None and not math.isfinite(nivel):
        raise ValueError("nivel no finito")
    if modo is not None and modo not in MODOS:
        raise ValueError("modo desconocido; no se sustituye la intención original")
    if condicion is not None and condicion not in OPERADORES:
        raise ValueError("condicion desconocida")
    if not all((provider, model, model_snapshot, prompt_version)):
        raise ValueError("Falta procedencia del modelo al registrar")
    if fecha_limite is not None:
        from datetime import date
        if len(fecha_limite) == 10:
            date.fromisoformat(fecha_limite)
        elif datetime.fromisoformat(fecha_limite).tzinfo is None:
            raise ValueError("fecha_limite necesita zona horaria")
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO predicciones (hilo_id, afirmacion, probabilidad, horizonte, tipo, "
            "instrumento, nivel_objetivo, creado, condicion, modo, fecha_limite, provider, "
            "model, model_snapshot, prompt_version, engine_version, schema_version, "
            "specification_source, estado_validacion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,2,'original','PENDING')",
            (hilo_id, afirmacion.strip(), p, horizonte.strip(), tipo, instrumento, nivel, _ahora(),
             condicion, modo, fecha_limite, provider, model, model_snapshot, prompt_version, ENGINE_VERSION),
        )
    return {"ok": True, "prediccion_id": cur.lastrowid}


def _filtro_skill(schema_version=None, resuelto_por=None, specification_source=None):
    sql, valores = "", []
    for campo, valor in (("schema_version", schema_version), ("resuelto_por", resuelto_por),
                         ("specification_source", specification_source)):
        if valor is not None:
            sql += f" AND {campo} = ?"
            valores.append(valor)
    return sql, valores


def desempeno_por_tipo(min_n: int = 5, **filtros):
    """Calibración desglosada por tipo de predicción — el feedback accionable."""
    filtro, valores = _filtro_skill(**filtros)
    filas = {}
    with _conn() as c:
        for t in TIPOS_PREDICCION:
            r = c.execute(
                "SELECT COUNT(*) n, AVG(probabilidad) p, AVG(resultado) real, AVG(brier) brier "
                "FROM predicciones WHERE resuelto IS NOT NULL AND tipo = ?" + filtro,
                [t, *valores],
            ).fetchone()
            if r["n"] and r["n"] >= min_n:
                filas[t] = {
                    "n": r["n"],
                    "declarada": round(r["p"], 2),
                    "observada": round(r["real"], 2),
                    "sesgo": round(r["p"] - r["real"], 2),
                    "brier": round(r["brier"], 3),
                }
    return filas


def recap_semanal():
    """Predicciones abiertas que vencen en los próximos 7 días — el repaso dominical."""
    from datetime import date, timedelta
    hoy = date.today()
    limite = (hoy + timedelta(days=7)).isoformat()
    hoy_s = hoy.isoformat()
    with _conn() as c:
        filas = c.execute(
            "SELECT id, afirmacion, probabilidad, horizonte, tipo, instrumento, nivel_objetivo "
            "FROM predicciones WHERE resuelto IS NULL AND horizonte IS NOT NULL "
            "AND length(horizonte)=10 AND horizonte <= ? ORDER BY horizonte", (limite,)
        ).fetchall()
    out = []
    for f in filas:
        d = dict(f)
        d["vencida"] = d["horizonte"] <= hoy_s
        out.append(d)
    return out


def todas_las_predicciones():
    """Todas las predicciones (abiertas y resueltas) para el panel completo del modelo,
    agrupadas por instrumento/tema. Incluye las que no tienen gráfico (eventos, geopolítica)."""
    from datetime import date
    hoy = date.today().isoformat()
    with _conn() as c:
        filas = c.execute(
            "SELECT * FROM predicciones ORDER BY creado DESC"
        ).fetchall()
    abiertas, resueltas = [], []
    for f in filas:
        d = dict(f)
        if d["resuelto"] is None:
            d["vencida"] = len(d["horizonte"] or "") == 10 and d["horizonte"] <= hoy
            abiertas.append(d)
        else:
            resueltas.append(d)
    # agrupar las abiertas por instrumento (o por tipo si no hay instrumento)
    grupos = {}
    for d in abiertas:
        clave = d.get("instrumento") or (
            "Tipos de interés" if d.get("tipo") == "evento" and any(
                b in (d["afirmacion"] or "").lower() for b in ("fed", "bce", "ecb", "boe", "boj"))
            else "Otros / geopolítica")
        grupos.setdefault(clave, []).append(d)
    # ordenar cada grupo por probabilidad desc
    for g in grupos.values():
        g.sort(key=lambda d: d["probabilidad"], reverse=True)
    return {
        "grupos": grupos,
        "n_abiertas": len(abiertas),
        "n_resueltas": len(resueltas),
        "resueltas_recientes": resueltas[:20],
    }


def predicciones_abiertas():
    from datetime import date
    hoy = date.today().isoformat()
    with _conn() as c:
        filas = c.execute(
            "SELECT * FROM predicciones WHERE resuelto IS NULL ORDER BY id"
        ).fetchall()
    out = []
    for f in filas:
        d = dict(f)
        d["vencida"] = len(d["horizonte"]) == 10 and d["horizonte"] <= hoy
        if d.get("instrumento"):
            import velas
            d["instrumento_grafico"] = velas.resolver_instrumento(d["instrumento"])
        else:
            d["instrumento_grafico"] = None
        out.append(d)
    out.sort(key=lambda d: d["probabilidad"], reverse=True)  # más probables primero
    return out


def resolver_prediccion(prediccion_id, resultado, nota="", por="agente", fecha_resolucion=None):
    """Compatibilidad legacy: el agente tiene prohibido resolver predicciones."""
    if por not in ("manual", "Alex"):
        return {"ok": False, "error": "El agente no puede resolver predicciones"}
    from validation import resolver_manual
    return resolver_manual(prediccion_id, resultado, nota, fecha_resolucion)


def predicciones_resueltas(limite: int = 30, **filtros):
    """Historial de predicciones ya resueltas, más recientes primero."""
    filtro, valores = _filtro_skill(**filtros)
    with _conn() as c:
        filas = c.execute(
            "SELECT * FROM predicciones WHERE resuelto IS NOT NULL " + filtro +
            " ORDER BY resuelto DESC, id DESC LIMIT ?",  # id desempata: _ahora() solo tiene segundos
            [*valores, limite],
        ).fetchall()
    return [dict(f) for f in filas]


def calibracion(**filtros):
    filtro, valores = _filtro_skill(**filtros)
    with _conn() as c:
        f = c.execute(
            "SELECT COUNT(*) n, AVG(brier) brier_medio, AVG(probabilidad) p_media, "
            "AVG(resultado) tasa_acierto FROM predicciones WHERE resuelto IS NOT NULL" + filtro, valores
        ).fetchone()
    if not f["n"]:
        return {"n": 0}
    sesgo = round(f["p_media"] - f["tasa_acierto"], 3)  # >0 sobreconfianza, <0 infraconfianza

    # ¿su "70%" ocurre el 70% de las veces? Tramos de probabilidad declarada.
    tramos = []
    with _conn() as c:
        for lo, hi, etiqueta in ((0.0, 0.4, "<40%"), (0.4, 0.6, "40-60%"),
                                 (0.6, 0.8, "60-80%"), (0.8, 1.01, ">80%")):
            t = c.execute(
                "SELECT COUNT(*) n, AVG(probabilidad) p, AVG(resultado) real "
                "FROM predicciones WHERE resuelto IS NOT NULL "
                "AND probabilidad >= ? AND probabilidad < ?" + filtro,
                [lo, hi, *valores],
            ).fetchone()
            if t["n"]:
                tramos.append({"tramo": etiqueta, "n": t["n"],
                               "declarada": round(t["p"], 2),
                               "observada": round(t["real"], 2)})

    # Habilidad: cuánto mejor que tirar una moneda / que la tasa base ingenua.
    # Brier de referencia (predecir siempre la frecuencia media): p*(1-p) con p=tasa_acierto.
    p_base = f["tasa_acierto"]
    brier_ref = p_base * (1 - p_base)
    brier_modelo = f["brier_medio"]
    if brier_ref > 0:
        habilidad = round((1 - brier_modelo / brier_ref) * 100, 1)  # % de mejora sobre la referencia
    else:
        habilidad = 0.0

    # Veredicto en cristiano según la habilidad y el sesgo
    if f["n"] < 15:
        nivel, veredicto = "sin-datos", "Aún hay pocas predicciones resueltas para juzgar con confianza. Sigue acumulando."
    elif habilidad <= 0:
        nivel, veredicto = "malo", "El modelo no bate a una referencia simple: sus probabilidades no aportan ventaja todavía."
    elif habilidad < 5:
        nivel, veredicto = "flojo", "Predice algo mejor que el azar, pero por poco margen. Útil con cautela."
    elif habilidad < 15:
        nivel, veredicto = "aceptable", "Predice mejor que el azar de forma consistente. Un edge modesto pero real."
    else:
        nivel, veredicto = "bueno", "Buena capacidad predictiva: sus probabilidades aportan ventaja clara sobre una referencia simple."
    # matiz sobre el sesgo
    if abs(sesgo) >= 0.08:
        direccion = "infla" if sesgo > 0 else "se queda corto en"
        veredicto += f" Ojo: {direccion} sus probabilidades (sesgo {sesgo:+.2f})."

    return {"n": f["n"], "brier_medio": round(f["brier_medio"], 4),
            "brier": round(f["brier_medio"], 4),
            "tasa_acierto": round(f["tasa_acierto"], 3), "sesgo": sesgo,
            "habilidad": habilidad, "nivel": nivel, "veredicto": veredicto,
            "tramos": tramos}


def calibracion_detalle(**filtros):
    """
    Veredicto de fiabilidad legible: además de Brier y sesgo, cuánta habilidad real
    tiene frente al referente honesto (acertar siempre la tasa base) y cómo traducir
    sus probabilidades a la práctica.
    """
    base = calibracion(**filtros)
    if not base.get("n"):
        return {"n": 0, "veredicto": "Sin datos suficientes todavía.", "nivel": "sin-datos"}

    tasa = base["tasa_acierto"]
    # Referente: predecir siempre la tasa base observada. Batirlo = tener criterio.
    brier_base = round(tasa * (1 - tasa), 4)
    habilidad = round((1 - base["brier_medio"] / brier_base) * 100, 1) if brier_base else 0.0

    if base["n"] < 20:
        nivel, veredicto = "sin-datos", (
            f"Solo {base['n']} predicciones resueltas: aún no hay muestra para juzgarlo. "
            "Trátalo como no verificado."
        )
    elif habilidad < 0:
        nivel, veredicto = "malo", (
            "Sus probabilidades no aportan información: aciertan menos que apostar "
            "siempre a la tasa base. No las uses para decidir."
        )
    elif habilidad < 10:
        nivel, veredicto = "flojo", (
            "Habilidad marginal. Sus probabilidades apenas superan al azar informado: "
            "sirven para ordenar temas, no para dimensionar riesgo."
        )
    elif habilidad < 25:
        nivel, veredicto = "aceptable", (
            "Habilidad real pero modesta. Sus probabilidades tienen información; "
            "úsalas como una opinión más, no como un número duro."
        )
    else:
        nivel, veredicto = "bueno", (
            "Habilidad clara frente al referente. Sus probabilidades merecen peso "
            "en tu proceso, siempre con el sesgo por tramos aplicado."
        )

    # Traducción práctica por tramo: qué significa de verdad cada nivel de confianza
    traduccion = []
    for t in base["tramos"]:
        desvio = round(t["observada"] - t["declarada"], 2)
        if t["n"] < 8:
            lectura = f"muestra corta (n={t['n']}): sin veredicto"
        elif abs(desvio) < 0.08:
            lectura = "fiable: tómalo tal cual"
        elif desvio < 0:
            lectura = f"infla {abs(desvio) * 100:.0f} puntos: descuéntalo"
        else:
            lectura = f"se queda corto {desvio * 100:.0f} puntos: súbelo"
        traduccion.append({**t, "desvio": desvio, "lectura": lectura})

    fiables = [t["tramo"] for t in traduccion if t["n"] >= 8 and abs(t["desvio"]) < 0.08]
    inflados = [t["tramo"] for t in traduccion if t["n"] >= 8 and t["desvio"] <= -0.08]

    return {
        "n": base["n"],
        "brier": base["brier_medio"],
        "brier_base": brier_base,
        "habilidad": habilidad,
        "sesgo": base["sesgo"],
        "tasa_acierto": tasa,
        "nivel": nivel,
        "veredicto": veredicto,
        "traduccion": traduccion,
        "tramos_fiables": fiables,
        "tramos_inflados": inflados,
        "por_tipo": desempeno_por_tipo(**filtros),
    }
