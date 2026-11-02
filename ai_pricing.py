"""Tarifas USD por millón de tokens, API directa estándar/global de Anthropic.

Fuente: https://platform.claude.com/docs/en/about-claude/pricing
Verificadas el 2026-09-08. No son facturas ni incluyen impuestos/descuentos.
Añadir versiones futuras no recalcula filas históricas de consumo_ia.
"""
PRICING_DATE = "2026-09-08"
PRICING_VERSION = "anthropic-standard-global-2026-09-08-v1"
MODEL_PRICING = {
    "claude-sonnet-4-6": dict(input=3.0, output=15.0, cache_read=.30,
                             cache_creation=3.75, cache_creation_1h=6.0),
    "claude-haiku-4-5-20251001": dict(input=1.0, output=5.0, cache_read=.10,
                                    cache_creation=1.25, cache_creation_1h=2.0),
    "claude-haiku-4-5": dict(input=1.0, output=5.0, cache_read=.10,
                           cache_creation=1.25, cache_creation_1h=2.0),
}


def calcular_coste(provider, model, tokens, usage):
    """NULL cuando no hay tarifa/usage seguro; caché separada del input base."""
    vacio = dict(coste_input=None, coste_output=None, coste_cache=None,
                 coste_total=None, ahorro_cache_estimado=None,
                 pricing_version=None, pricing_date=None)
    rates = MODEL_PRICING.get(model) if provider == "anthropic" else None
    if not rates or tokens['input_tokens'] is None or tokens['output_tokens'] is None:
        return vacio
    if usage.get('service_tier') not in (None, 'standard'):
        return vacio
    if usage.get('inference_geo') not in (None, 'global'):
        return vacio
    # Herramientas de servidor tienen cargos adicionales; no presentar total incompleto.
    if any((usage.get('server_tool_use') or {}).values()):
        return vacio
    creados = tokens['cache_creation_tokens']
    cinco, hora = tokens['cache_creation_5m_tokens'], tokens['cache_creation_1h_tokens']
    if creados and (cinco is None or hora is None or cinco + hora != creados):
        return vacio  # no inferir TTL ni asignar una tarifa de escritura arbitraria
    cinco, hora = cinco or 0, hora or 0
    entrada = tokens['input_tokens'] * rates['input'] / 1_000_000
    salida = tokens['output_tokens'] * rates['output'] / 1_000_000
    cache = (tokens['cache_read_tokens'] * rates['cache_read']
             + cinco * rates['cache_creation'] + hora * rates['cache_creation_1h']) / 1_000_000
    # Contrafactual sin caché; puede ser negativo si todavía predominan escrituras.
    sin_cache = (tokens['cache_read_tokens'] + creados) * rates['input'] / 1_000_000
    return dict(coste_input=entrada, coste_output=salida, coste_cache=cache,
                coste_total=entrada + salida + cache, ahorro_cache_estimado=sin_cache-cache,
                pricing_version=PRICING_VERSION, pricing_date=PRICING_DATE)
