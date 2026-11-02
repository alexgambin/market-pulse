"""Observabilidad pasiva de llamadas IA. Nunca altera kwargs, respuestas o errores.

Anthropic input_tokens excluye lecturas/escrituras de caché: total=sumatorio de
los cuatro contadores. Usage ausente es NULL, no consumo cero ni una estimación.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import json
import logging
from zoneinfo import ZoneInfo

import db
from ai_pricing import calcular_coste
from migrations import migrate_observability

log = logging.getLogger(__name__)
COMPONENTES = frozenset({
    'pasada_programada', 'pasada_vigilante', 'pasada_extra', 'pasada_manual',
    'triaje', 'informe_manana', 'informe_noche', 'informe_ahora',
    'evento_prevision', 'evento_reaccion', 'otro',
})
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens', 'total_tokens')


@dataclass
class Contexto:
    componente: str = 'otro'
    subcomponente: str | None = None
    metadatos: dict = field(default_factory=dict)
    ejecucion_id: int | None = None
    noticias_consideradas: int = 0
    noticias_candidatas: int = 0
    noticias_relevantes: int = 0
    hilos_creados: set = field(default_factory=set)
    hilos_actualizados: set = field(default_factory=set)
    predicciones_creadas: set = field(default_factory=set)
    registros_perdidos: int = 0

    def __post_init__(self):
        if self.componente not in COMPONENTES:
            self.componente = 'otro'


def error_seguro(exc):
    """Nunca loguear str(exc), bodies o URLs: pueden contener claves/prompts."""
    estado = getattr(exc, 'status_code', None)
    return type(exc).__name__ + (f' HTTP {estado}' if type(estado) is int else '')


def init_consumo():
    try:
        with db._conn() as c:
            c.execute('PRAGMA busy_timeout=100')
            c.execute('BEGIN IMMEDIATE')
            migrate_observability(c)
        return True
    except Exception as exc:
        log.warning('[AI COST] migracion fallida: %s', error_seguro(exc))
        return False


def iniciar_ejecucion(componente, subcomponente=None, metadatos=None):
    ctx = Contexto(componente, subcomponente, metadatos or {})
    try:
        with db._conn() as c:
            c.execute('PRAGMA busy_timeout=100')
            cur = c.execute('''INSERT INTO ejecuciones_agente
                (inicio,tipo_ejecucion,subcomponente,metadatos) VALUES (?,?,?,?)''',
                (db._ahora(), ctx.componente, ctx.subcomponente, json.dumps(ctx.metadatos)))
            ctx.ejecucion_id = cur.lastrowid
    except Exception as exc:
        log.warning('[AI COST] inicio no registrado: %s', error_seguro(exc))
    return ctx


def finalizar_ejecucion(ctx, success, error=None):
    if ctx.ejecucion_id is None:
        return
    try:
        with db._conn() as c:
            c.execute('PRAGMA busy_timeout=100')
            costes = c.execute('''SELECT count(*) n, count(coste_total) conocidos,
                coalesce(sum(coste_total),0) coste FROM consumo_ia WHERE ejecucion_id=?''',
                (ctx.ejecucion_id,)).fetchone()
            total = costes['coste'] if costes['n'] == costes['conocidos'] and not ctx.registros_perdidos else None
            c.execute('''UPDATE ejecuciones_agente SET fin=?, noticias_consideradas=?,
                noticias_candidatas=?, noticias_relevantes=?, hilos_creados=?, hilos_actualizados=?,
                predicciones_creadas=?, coste_ia_total=?, success=?, error=?, registros_perdidos=? WHERE id=?''',
                (db._ahora(), ctx.noticias_consideradas, ctx.noticias_candidatas, ctx.noticias_relevantes,
                 len(ctx.hilos_creados), len(ctx.hilos_actualizados), len(ctx.predicciones_creadas),
                 total, int(success), error_seguro(error) if error else None, ctx.registros_perdidos, ctx.ejecucion_id))
    except Exception as exc:
        log.warning('[AI COST] fin no registrado: %s', error_seguro(exc))


def contar_noticias(ctx, **contadores):
    if ctx is None:
        return
    for nombre in ('noticias_consideradas', 'noticias_candidatas', 'noticias_relevantes'):
        if nombre in contadores:
            setattr(ctx, nombre, contadores[nombre])


def contar_herramienta(ctx, nombre, args, salida):
    """Cuenta objetos distintos confirmados por herramientas existentes, sin consultas IA."""
    if ctx is None or not isinstance(salida, dict) or not salida.get('ok'):
        return
    try:
        if nombre == 'crear_hilo':
            ctx.hilos_creados.add(salida['hilo_id'])
        elif nombre == 'actualizar_hilo':
            ctx.hilos_actualizados.add(args['hilo_id'])
        elif nombre == 'registrar_prediccion':
            ctx.predicciones_creadas.add(salida['prediccion_id'])
    except Exception as exc:
        log.warning('[AI COST] productividad no registrada: %s', error_seguro(exc))


def _dict(obj):
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(mode='json')
    return vars(obj) if hasattr(obj, '__dict__') else {}


def _usage(obj, error=False):
    usage = _dict(obj).get('usage')
    if usage is None and error:
        usage = _dict(getattr(obj, 'body', None)).get('usage')
    return _dict(usage)


def _tokens(usage):
    if not usage:
        return {k: None for k in (*TOKEN_FIELDS, 'cache_creation_5m_tokens', 'cache_creation_1h_tokens')}
    def counter(value):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('usage invalido')
        return value
    result = dict(input_tokens=counter(usage.get('input_tokens')), output_tokens=counter(usage.get('output_tokens')),
                  cache_read_tokens=counter(usage.get('cache_read_input_tokens') or 0),
                  cache_creation_tokens=counter(usage.get('cache_creation_input_tokens') or 0))
    result['total_tokens'] = sum(result.values()) if all(v is not None for v in result.values()) else None
    creation = _dict(usage.get('cache_creation'))
    result['cache_creation_5m_tokens'] = counter(creation.get('ephemeral_5m_input_tokens'))
    result['cache_creation_1h_tokens'] = counter(creation.get('ephemeral_1h_input_tokens'))
    return result


def registrar(contexto, componente, modelo, respuesta=None, error=None, timestamp=None):
    """Best effort: errores de persistencia no salen hacia el código analítico."""
    try:
        usage = _usage(error if error is not None else respuesta, error=error is not None)
        tokens = _tokens(usage)
        model = getattr(respuesta, 'model', None) or modelo
        coste = calcular_coste('anthropic', model, tokens, usage)
        row = dict(timestamp=timestamp or db._ahora(), ejecucion_id=contexto.ejecucion_id,
                   componente=componente or contexto.componente, subcomponente=contexto.subcomponente,
                   origen=contexto.componente, provider='anthropic', model=model, model_solicitado=modelo,
                   **tokens, **coste, success=int(error is None), error=error_seguro(error) if error else None,
                   request_id=getattr(respuesta, '_request_id', None),
                   usage_json=json.dumps(usage, ensure_ascii=False), metadatos=json.dumps(contexto.metadatos))
        if row['componente'] not in COMPONENTES:
            row['componente'] = 'otro'
        with db._conn() as c:
            c.execute('PRAGMA busy_timeout=100')
            c.execute(f"INSERT INTO consumo_ia ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
        log.info('[AI COST] componente=%s model=%s in=%s out=%s cost=%s',
                 row['componente'], model, tokens['input_tokens'], tokens['output_tokens'], coste['coste_total'])
        return True
    except Exception as exc:
        contexto.registros_perdidos += 1
        log.warning('[AI COST] consumo no registrado: %s', error_seguro(exc))
        return False


async def llamar(crear, contexto=None, *, componente=None, **kwargs):
    """Una invocación SDK, mismos argumentos y misma respuesta/excepción; sin retry nuevo."""
    contexto = contexto or Contexto()
    timestamp = db._ahora()
    try:
        response = await crear(**kwargs)
    except BaseException as exc:
        try:
            await asyncio.to_thread(registrar, contexto, componente, kwargs.get('model', ''),
                                    error=exc, timestamp=timestamp)
        except Exception as fallo:
            contexto.registros_perdidos += 1
            log.warning('[AI COST] observabilidad fallida: %s', error_seguro(fallo))
        raise
    try:
        await asyncio.to_thread(registrar, contexto, componente, kwargs.get('model', ''),
                                respuesta=response, timestamp=timestamp)
    except Exception as exc:
        contexto.registros_perdidos += 1
        log.warning('[AI COST] observabilidad fallida: %s', error_seguro(exc))
    return response


def resumen(horas=24, dia=None, zona='Europe/Madrid', ahora=None):
    """Agregados desde contadores reales. Totales incompletos nunca se presentan como cero."""
    ahora = ahora or datetime.now(timezone.utc)
    if dia:
        local = ZoneInfo(zona)
        inicio = datetime.combine(datetime.fromisoformat(dia).date(), time.min, local)
        fin = inicio + timedelta(days=1)
        inicio, fin = inicio.astimezone(timezone.utc), fin.astimezone(timezone.utc)
    else:
        if horas not in (24, 48):
            raise ValueError('horas debe ser 24 o 48')
        inicio, fin = ahora-timedelta(hours=horas), ahora
    with db._conn() as c:
        filas = [dict(r) for r in c.execute('SELECT * FROM consumo_ia WHERE timestamp >= ? AND timestamp < ? ORDER BY id',
                                           (inicio.isoformat(), fin.isoformat()))]
        ejecuciones = [dict(r) for r in c.execute('SELECT * FROM ejecuciones_agente WHERE inicio >= ? AND inicio < ? ORDER BY id',
                                               (inicio.isoformat(), fin.isoformat()))]
        primero = c.execute('SELECT min(timestamp) FROM consumo_ia').fetchone()[0]
    def agregar(rows):
        conocidos = [r['coste_total'] for r in rows if r['coste_total'] is not None]
        subtotal = sum(conocidos)
        completo = len(conocidos) == len(rows)
        result = dict(llamadas=len(rows), fallidas=sum(not r['success'] for r in rows),
                      llamadas_sin_coste=len(rows)-len(conocidos), llamadas_sin_usage=sum(r['total_tokens'] is None for r in rows),
                      coste_conocido=subtotal, coste_total=subtotal if completo else None,
                      ahorro_cache_estimado=sum(r['ahorro_cache_estimado'] or 0 for r in rows) if completo else None)
        for key in TOKEN_FIELDS:
            values = [r[key] for r in rows if r[key] is not None]
            result[key] = sum(values) if values or not rows else None
        total_entrada = sum(result[k] or 0 for k in ('input_tokens','cache_read_tokens','cache_creation_tokens'))
        result['porcentaje_input_cache_read'] = 100*(result['cache_read_tokens'] or 0)/total_entrada if total_entrada else None
        return result
    total = agregar(filas)
    groups = {}
    for r in filas:
        key = (r['componente'], r['subcomponente'], r['model'])
        groups.setdefault(key, []).append(r)
    grupos = []
    for (component, sub, model), rows in groups.items():
        g = dict(componente=component, subcomponente=sub, model=model, **agregar(rows))
        g['porcentaje_del_coste'] = 100*g['coste_total']/total['coste_total'] if total['coste_total'] and g['coste_total'] is not None else None
        g['porcentaje_coste_conocido'] = 100*g['coste_conocido']/total['coste_conocido'] if total['coste_conocido'] else None
        grupos.append(g)
    for e in ejecuciones:
        e['coste_por_hilo_actualizado'] = e['coste_ia_total']/e['hilos_actualizados'] if e['hilos_actualizados'] and e['coste_ia_total'] is not None else None
        e['coste_por_prediccion'] = e['coste_ia_total']/e['predicciones_creadas'] if e['predicciones_creadas'] and e['coste_ia_total'] is not None else None
    observado = max(inicio, datetime.fromisoformat(primero)) if primero else None
    horas_observadas = max(0, (min(fin, ahora)-observado).total_seconds()/3600) if observado else 0
    ritmo = total['coste_total']/horas_observadas if horas_observadas and total['coste_total'] is not None else None
    return dict(inicio=inicio.isoformat(), fin=fin.isoformat(), moneda='USD', total=total,
                grupos=grupos, ejecuciones=ejecuciones, horas_observadas=horas_observadas,
                proyeccion_diaria=ritmo*24 if ritmo is not None else None,
                proyeccion_mensual_30d=ritmo*24*30 if ritmo is not None else None)
