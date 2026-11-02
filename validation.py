"""Validación determinista e independiente del agente.

resultado describe realidad: 1 ocurrió, 0 no ocurrió, NULL sin resultado.
Brier=(probabilidad-resultado)**2 mide la calidad, incluso si resultado=0.
Velas diarias: nunca usamos el día de creación ni velas aún incompletas.
No hay reconstrucción de contratos legacy ni resolución basada en `tipo`.
"""
import json
import math
import operator
from datetime import date, datetime, time, timedelta, timezone

import db
import precios
from config import DATA_ERROR, ENGINE_VERSION, MODOS_RESOLUBLES, NEEDS_REVIEW, OPERADORES, PENDING, RESOLVED

COMPARAR = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}


def resoluble_por_precio(p):
    return bool(p.get("instrumento") in precios.INSTRUMENTOS
                and p.get("condicion") in OPERADORES
                and p.get("nivel_objetivo") is not None
                and p.get("modo") in MODOS_RESOLUBLES and p.get("fecha_limite"))


def _timestamp(value):
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError("timestamp sin zona horaria")
    return value


def _salida(estado, motivo, resultado=None, fecha=None, **evidencia):
    return dict(estado_validacion=estado, resultado=resultado, fecha_resolucion=fecha,
                motivo=motivo, evidencia=evidencia)


def evaluar(p, obtener_serie=None, ahora=None):
    """Evalúa sin escribir. toca usa extremos; cierra sólo el cierre final.

    Fecha límite YYYY-MM-DD: sesión inclusiva; esperamos hasta las 12 UTC del
    día siguiente para dar por completadas velas diarias en cualquier zona.
    Timestamp límite: se exige zona y sólo entran velas cuyo fin sea demostrable.
    La espera conservadora no cambia la fecha del hecho, sólo su detección.
    """
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        raise ValueError("ahora debe incluir zona horaria")
    if not resoluble_por_precio(p):
        return _salida(NEEDS_REVIEW, "Falta contrato de precio soportado")
    if p.get("schema_version") != 2 and p.get("specification_source") != "manual":
        return _salida(NEEDS_REVIEW, "Legacy sin contrato estructurado original")
    try:
        nivel = float(p["nivel_objetivo"])
        if not math.isfinite(nivel):
            raise ValueError("nivel no finito")
        creado = datetime.fromisoformat(p["creado"])
        # Hora legacy ambigua: conservamos fecha, sin inventarle una zona.
        dia_creacion = max(creado.date(), creado.astimezone(timezone.utc).date()) if creado.tzinfo else creado.date()
        inicio = dia_creacion + timedelta(days=1)
        limite = p["fecha_limite"]
        limite_ts = _timestamp(limite) if len(limite) > 10 else None
        fin = limite_ts.date() if limite_ts else date.fromisoformat(limite)
        vencimiento = limite_ts or datetime.combine(fin + timedelta(days=1), time(12), timezone.utc)
    except (ValueError, TypeError, KeyError, OverflowError):
        return _salida(NEEDS_REVIEW, "Fechas o nivel inválidos/ambiguos")
    vencida = ahora >= vencimiento
    hasta = min(fin, ahora.astimezone(timezone.utc).date() - timedelta(days=1))
    if hasta < inicio:
        return _salida(DATA_ERROR if vencida else PENDING, "Todavía no hay ventana diaria completa")
    try:
        serie = (obtener_serie or precios.historial_validacion)(p["instrumento"], inicio, hasta)
        barras = []
        descartes_temporales = []
        invalidas = 0
        fechas_en_ventana = []
        for b in serie:
            try:
                dia = date.fromisoformat(b["fecha"])
                if not inicio <= dia <= hasta:
                    continue
                fechas_en_ventana.append(dia.isoformat())
                valores = [float(b[k]) for k in ("high", "low", "close")]
                if not all(math.isfinite(v) for v in valores):
                    invalidas += 1
                    continue
                high, low, close = valores
                if not low <= close <= high:
                    invalidas += 1
                    continue
                inicio_ts = _timestamp(b["inicio"]) if b.get("inicio") else None
                cierre_ts = _timestamp(b["fin"]) if b.get("fin") else None
                if inicio_ts and not creado.tzinfo:
                    invalidas += 1
                    continue
                motivo_temporal = None
                if inicio_ts and inicio_ts <= creado:
                    motivo_temporal = "INTERVALO_SOLAPA_CREACION"
                elif cierre_ts and cierre_ts > ahora:
                    motivo_temporal = "INTERVALO_AUN_INCOMPLETO"
                if motivo_temporal:
                    if inicio_ts and cierre_ts and inicio_ts >= cierre_ts:
                        invalidas += 1
                        continue
                    descartes_temporales.append(dict(
                        fecha=dia.isoformat(), inicio=b.get("inicio"),
                        fin=b.get("fin"), motivo=motivo_temporal))
                    continue
                if limite_ts and (cierre_ts is None or cierre_ts > limite_ts):
                    continue
                barras.append(dict(fecha=dia.isoformat(), high=high, low=low, close=close,
                                   inicio=b.get("inicio"), fin=b.get("fin")))
            except (ValueError, TypeError, KeyError):
                invalidas += 1
                continue
        barras.sort(key=lambda b: b["fecha"])
        if len({b['fecha'] for b in barras}) != len(barras):
            return _salida(DATA_ERROR, "Serie con sesiones duplicadas")
    except Exception as exc:
        return _salida(DATA_ERROR, "Fallo del proveedor", error=type(exc).__name__)
    evidencia = dict(instrumento=p["instrumento"], simbolo=precios.INSTRUMENTOS[p["instrumento"]],
                     fuente="yfinance", intervalo="1d", auto_adjust=False,
                     inicio=inicio.isoformat(), hasta=hasta.isoformat(), barras=barras)
    if not barras:
        # No confundir una descarga vacía/defectuosa con datos válidos que
        # todavía no son utilizables bajo el margen temporal del baseline.
        duplicadas = len(fechas_en_ventana) != len(set(fechas_en_ventana))
        evidencia["diagnostico_temporal"] = dict(
            version="data-availability-v1", invalidas=invalidas,
            fechas_duplicadas=duplicadas, descartadas=descartes_temporales)
        espera_por_solapamiento = descartes_temporales and all(
            d["motivo"] == "INTERVALO_SOLAPA_CREACION" and d["fin"]
            and _timestamp(d["fin"]) <= ahora for d in descartes_temporales)
        if not vencida and espera_por_solapamiento and not invalidas and not duplicadas:
            return _salida(PENDING, "Esperando primera sesión completa posterior a la predicción",
                           **evidencia)
        return _salida(DATA_ERROR, "Sin barras válidas en la ventana", **evidencia)
    comparar = COMPARAR[p["condicion"]]
    if p["modo"] == "toca":
        campo = "high" if p["condicion"] in (">=", ">") else "low"
        for b in barras:
            if comparar(b[campo], nivel):
                return _salida(RESOLVED, "Nivel tocado", 1, b["fecha"], **evidencia)
        if not vencida:
            return _salida(PENDING, "Nivel aún no tocado; plazo abierto", **evidencia)
        return _salida(RESOLVED, "Plazo vencido sin toque observado", 0, fin.isoformat(), **evidencia)
    if not vencida:
        return _salida(PENDING, "cierra espera al final del horizonte", **evidencia)
    ultima = barras[-1]
    return _salida(RESOLVED, "Cierre final del horizonte", int(comparar(ultima["close"], nivel)),
                   ultima["fecha"], **evidencia)


def guardar_evaluacion(prediccion_id, evaluacion, por="automatico", fuente=None):
    """Escritura atómica y auditoría. Nunca sobrescribe una resolución final."""
    if por not in ("automatico", "manual"):
        raise ValueError("Autor de resolución no permitido")
    payload = json.dumps(evaluacion, ensure_ascii=False, sort_keys=True, allow_nan=False)
    with db._conn() as c:
        c.execute("BEGIN IMMEDIATE")
        p = c.execute("SELECT * FROM predicciones WHERE id=?", (prediccion_id,)).fetchone()
        if p is None or p["resuelto"] is not None:
            return {"ok": False, "error": "Predicción inexistente o ya resuelta"}
        if por == "manual" and p["estado_validacion"] != NEEDS_REVIEW:
            return {"ok": False, "error": "Sólo se resuelven manualmente predicciones en NEEDS_REVIEW"}
        if por == "automatico" and p["resuelto_por"] == "manual":
            return {"ok": False, "error": "Revisión manual preservada"}
        estado = evaluacion["estado_validacion"]
        resultado = evaluacion["resultado"]
        final = estado == RESOLVED
        brier = (p["probabilidad"] - resultado) ** 2 if final else None
        ultimo = c.execute("SELECT evidencia FROM validacion_auditoria WHERE prediccion_id=? ORDER BY id DESC LIMIT 1",
                           (prediccion_id,)).fetchone()
        if ultimo and ultimo["evidencia"] == payload:
            return {"ok": True, "sin_cambios": True}
        escrito = db._ahora()
        c.execute("""UPDATE predicciones SET estado_validacion=?, resultado=?, brier=?,
            resuelto=?, fecha_resolucion=?, resuelto_por=?, fuente_resolucion=?
            WHERE id=? AND resuelto IS NULL""",
            (estado, resultado, brier, escrito if final else None,
             evaluacion["fecha_resolucion"], por if final or por == "manual" else None,
             fuente or payload, prediccion_id))
        c.execute("INSERT INTO validacion_auditoria (prediccion_id, registrado, engine_version, evidencia) VALUES (?,?,?,?)",
                  (prediccion_id, escrito, ENGINE_VERSION, payload))
    return {"ok": True, "estado_validacion": estado, "brier": brier}


def ejecutar_validacion(ahora=None, obtener_serie=None):
    """Job reintentable: DATA_ERROR sigue siendo técnico; NEEDS_REVIEW es humano."""
    with db._conn() as c:
        pendientes = [dict(r) for r in c.execute("""SELECT * FROM predicciones
            WHERE resuelto IS NULL AND resultado IS NULL
            AND (resuelto_por IS NULL OR resuelto_por != 'manual') ORDER BY id""")]
    conteo = {}
    cache = {}
    proveedor = obtener_serie or precios.historial_validacion
    def serie(inst, inicio, fin):
        key = (inst, inicio, fin)
        if key not in cache:
            cache[key] = proveedor(inst, inicio, fin)
        return cache[key]
    for p in pendientes:
        e = evaluar(p, serie, ahora)
        guardar_evaluacion(p["id"], e)
        conteo[e["estado_validacion"]] = conteo.get(e["estado_validacion"], 0) + 1
    return conteo


def cola_revision():
    with db._conn() as c:
        filas = c.execute("SELECT * FROM predicciones WHERE estado_validacion IN ('NEEDS_REVIEW','DATA_ERROR') ORDER BY id").fetchall()
    return {estado: [dict(r) for r in filas if r["estado_validacion"] == estado]
            for estado in (NEEDS_REVIEW, DATA_ERROR)}


def resolver_manual(prediccion_id, resultado, fuente, fecha_resolucion=None):
    """OCURRIÓ=1, NO OCURRIÓ=0, NO RESOLUBLE=NULL. Nunca acertó/falló."""
    if resultado is not None and (type(resultado) is not int or resultado not in (0, 1)):
        return {"ok": False, "error": "resultado debe ser 0, 1 o NULL"}
    if not isinstance(fuente, str) or not fuente.strip():
        return {"ok": False, "error": "Indica fuente y justificación verificable"}
    if resultado is not None:
        try:
            if len(fecha_resolucion) == 10:
                if date.fromisoformat(fecha_resolucion) > datetime.now(timezone.utc).date():
                    raise ValueError()
            elif _timestamp(fecha_resolucion) > datetime.now(timezone.utc):
                raise ValueError()
        except (ValueError, TypeError):
            return {"ok": False, "error": "Indica la fecha real del hecho (no futura)"}
    e = _salida(NEEDS_REVIEW if resultado is None else RESOLVED, fuente.strip(), resultado,
                fecha_resolucion if resultado is not None else None, fuente=fuente.strip(), autor="manual")
    return guardar_evaluacion(prediccion_id, e, por="manual", fuente=fuente.strip())
