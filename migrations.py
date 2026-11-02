"""Migración aditiva e idempotente del contrato v2.

resultado: 1 ocurrió, 0 no ocurrió, NULL desconocido; no mide acierto.
resuelto: timestamp de escritura. fecha_resolucion: fecha del hecho observado.
El histórico conserva sus resultados/Brier y nunca adquiere un contrato inferido.
"""
from config import CAMPOS_INMUTABLES


def migrate_predictions(c):
    c.execute("BEGIN IMMEDIATE")
    columns = {r[1] for r in c.execute("PRAGMA table_info(predicciones)")}
    additions = {
        "nota": "TEXT", "tipo": "TEXT DEFAULT 'otro'", "instrumento": "TEXT",
        "nivel_objetivo": "REAL", "condicion": "TEXT", "modo": "TEXT",
        "fecha_limite": "TEXT", "provider": "TEXT", "model": "TEXT",
        "model_snapshot": "TEXT", "prompt_version": "TEXT", "engine_version": "TEXT",
        "schema_version": "INTEGER DEFAULT 2", "specification_source": "TEXT",
        "estado_validacion": "TEXT DEFAULT 'PENDING'", "resuelto_por": "TEXT",
        "fuente_resolucion": "TEXT", "fecha_resolucion": "TEXT",
    }
    for name, sql_type in additions.items():
        if name not in columns:
            c.execute(f"ALTER TABLE predicciones ADD COLUMN {name} {sql_type}")
    if "schema_version" not in columns:
        c.execute("""UPDATE predicciones SET schema_version=1,
            specification_source='legacy',
            resuelto_por=CASE WHEN resuelto IS NOT NULL THEN 'agente' ELSE NULL END,
            estado_validacion=CASE WHEN resuelto IS NOT NULL THEN 'RESOLVED'
                                  ELSE 'NEEDS_REVIEW' END""")
    c.execute("DROP TRIGGER IF EXISTS predicciones_inmutables")
    changes = " OR ".join(f"NEW.{k} IS NOT OLD.{k}" for k in CAMPOS_INMUTABLES)
    c.execute(f"""CREATE TRIGGER predicciones_inmutables BEFORE UPDATE ON predicciones
        WHEN {changes} BEGIN SELECT RAISE(ABORT, 'contrato de prediccion inmutable'); END""")
    # ALTER no añade CHECK a columnas existentes: triggers para INSERT/UPDATE.
    for action in ("INSERT", "UPDATE"):
        c.execute(f"""CREATE TRIGGER IF NOT EXISTS predicciones_valores_{action.lower()}
            BEFORE {action} ON predicciones WHEN
            (NEW.resultado IS NOT NULL AND (typeof(NEW.resultado) != 'integer'
                OR NEW.resultado NOT IN (0,1)))
            OR NEW.estado_validacion IS NULL
            OR NEW.estado_validacion NOT IN ('PENDING','NEEDS_REVIEW','DATA_ERROR','RESOLVED')
            OR (NEW.estado_validacion='RESOLVED' AND (NEW.resultado IS NULL OR NEW.resuelto IS NULL))
            OR (NEW.estado_validacion!='RESOLVED' AND (NEW.resultado IS NOT NULL OR NEW.resuelto IS NOT NULL))
            BEGIN SELECT RAISE(ABORT, 'estado/resultado de prediccion invalido'); END""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS predicciones_resolucion_final
        BEFORE UPDATE ON predicciones WHEN OLD.resuelto IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'prediccion ya resuelta'); END""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS predicciones_procedencia_insert
        BEFORE INSERT ON predicciones WHEN NEW.schema_version IS NOT 2
        OR NEW.estado_validacion IS NOT 'PENDING'
        OR NEW.specification_source IS NOT 'original'
        OR NEW.provider IS NULL OR NEW.model IS NULL OR NEW.model_snapshot IS NULL
        OR NEW.prompt_version IS NULL OR NEW.engine_version IS NULL
        OR NEW.probabilidad IS NULL OR NEW.probabilidad < 0 OR NEW.probabilidad > 1
        OR (NEW.modo IS NOT NULL AND NEW.modo NOT IN ('toca','cierra','mantiene'))
        OR (NEW.condicion IS NOT NULL AND NEW.condicion NOT IN ('>=','<=','>','<'))
        BEGIN SELECT RAISE(ABORT, 'nueva prediccion requiere contrato y procedencia v2'); END""")
    c.execute("""CREATE TABLE IF NOT EXISTS validacion_auditoria (
        id INTEGER PRIMARY KEY, prediccion_id INTEGER NOT NULL REFERENCES predicciones(id),
        registrado TEXT NOT NULL, engine_version TEXT NOT NULL, evidencia TEXT NOT NULL
    )""")


def migrate_observability(c):
    """Tablas independientes: no modifica predicciones, análisis ni sus triggers."""
    c.execute("""CREATE TABLE IF NOT EXISTS ejecuciones_agente (
        id INTEGER PRIMARY KEY,
        inicio TEXT NOT NULL, fin TEXT,
        tipo_ejecucion TEXT NOT NULL, subcomponente TEXT,
        noticias_consideradas INTEGER NOT NULL DEFAULT 0,
        noticias_candidatas INTEGER NOT NULL DEFAULT 0,
        noticias_relevantes INTEGER NOT NULL DEFAULT 0,
        hilos_creados INTEGER NOT NULL DEFAULT 0,
        hilos_actualizados INTEGER NOT NULL DEFAULT 0,
        predicciones_creadas INTEGER NOT NULL DEFAULT 0,
        coste_ia_total REAL, success INTEGER, error TEXT,
        metadatos TEXT, registros_perdidos INTEGER NOT NULL DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS consumo_ia (
        id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
        ejecucion_id INTEGER REFERENCES ejecuciones_agente(id),
        componente TEXT NOT NULL, subcomponente TEXT, origen TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT NOT NULL, model_solicitado TEXT,
        input_tokens INTEGER, output_tokens INTEGER,
        cache_read_tokens INTEGER, cache_creation_tokens INTEGER, total_tokens INTEGER,
        cache_creation_5m_tokens INTEGER, cache_creation_1h_tokens INTEGER,
        coste_input REAL, coste_output REAL, coste_cache REAL, coste_total REAL,
        ahorro_cache_estimado REAL, pricing_version TEXT, pricing_date TEXT,
        success INTEGER NOT NULL, error TEXT, request_id TEXT, usage_json TEXT,
        metadatos TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS consumo_ia_timestamp ON consumo_ia(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS consumo_ia_ejecucion ON consumo_ia(ejecucion_id)")


def migrate_positioning(c):
    """Esquema aislado y versionado; no altera tablas/triggers de predicciones."""
    from positioning.schema import migrate
    migrate(c)
