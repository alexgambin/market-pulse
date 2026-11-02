"""Contratos comunes; sin dependencias del agente ni SDK de IA."""
OPERADORES = (">=", "<=", ">", "<")
# toca: cualquier HIGH/LOW de la ventana; cierra: sólo CLOSE final del horizonte.
# mantiene: intención conservada, contrato exacto aún sin resolver automático.
MODOS = ("toca", "cierra", "mantiene")
MODOS_RESOLUBLES = ("toca", "cierra")
PENDING = "PENDING"
NEEDS_REVIEW = "NEEDS_REVIEW"
DATA_ERROR = "DATA_ERROR"
RESOLVED = "RESOLVED"
ESTADOS_VALIDACION = (PENDING, NEEDS_REVIEW, DATA_ERROR, RESOLVED)
ENGINE_VERSION = "price-daily-v1"
PROMPT_VERSION = "predicciones-v2-2026-09-08"
CAMPOS_INMUTABLES = (
    "afirmacion", "instrumento", "condicion", "nivel_objetivo", "modo",
    "fecha_limite", "horizonte", "probabilidad", "creado", "provider", "model",
    "model_snapshot", "prompt_version", "engine_version", "schema_version",
    "specification_source", "tipo", "nota",
)
CAMPOS_RESOLUCION = (
    "resultado", "estado_validacion", "resuelto", "fecha_resolucion",
    "resuelto_por", "fuente_resolucion", "brier",
)
ZONA = "Europe/Madrid"

# Medición de costes: silenciar el transporte sin desactivar los informes.
TELEGRAM_ENABLED = False

# Experimento futuro: permanecer OFF durante el baseline de Validation v2.
# En Fase 1 no hay herramienta registrada, incluso si se modifica esta constante.
POSITIONING_AGENT_ENABLED = False
