"""Migración v1 aditiva, transaccional y sin efectos sobre el baseline.

Los escritores de Fases 2–5 deberán validar además los contratos entre documentos
JSON. Esta migración no habilita fuentes, fórmulas, ni captura de predicciones.
"""
from datetime import datetime, timezone

from .contracts import (AVAILABILITY_POLICY, AVAILABILITY_VERSION, SCHEMA_VERSION,
                        canonical_json, content_hash)


TABLES = (
    """CREATE TABLE IF NOT EXISTS positioning_versions (
        version TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('SCHEMA','CALCULATION','AVAILABILITY','MAPPING','CALENDAR')),
        specification_json TEXT NOT NULL CHECK(json_valid(specification_json)),
        specification_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS positioning_series (
        series_id TEXT PRIMARY KEY,
        instrument TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source='CFTC'),
        dataset_id TEXT NOT NULL,
        report_type TEXT NOT NULL,
        report_scope TEXT NOT NULL CHECK(report_scope='FUTURES_ONLY'),
        market_code TEXT NOT NULL CHECK(length(market_code)>0),
        participant_category TEXT NOT NULL,
        mapping_version TEXT NOT NULL REFERENCES positioning_versions(version),
        source_name TEXT NOT NULL,
        price_symbol TEXT NOT NULL,
        relationship TEXT NOT NULL CHECK(relationship IN ('UNDERLYING','INVERSE_QUOTE')),
        unit TEXT NOT NULL CHECK(unit='CONTRACTS'),
        validation_evidence_json TEXT NOT NULL CHECK(json_valid(validation_evidence_json)),
        valid_from TEXT NOT NULL,
        valid_until TEXT,
        created_at TEXT NOT NULL,
        CHECK(valid_until IS NULL OR valid_until >= valid_from),
        CHECK((report_type='LEGACY' AND dataset_id='6dca-aqww' AND participant_category IN
                 ('NON_COMMERCIAL','COMMERCIAL','NON_REPORTABLE'))
          OR (report_type='DISAGGREGATED' AND dataset_id='72hh-3qpy' AND participant_category IN
                 ('PRODUCER_MERCHANT_PROCESSOR_USER','SWAP_DEALER','MANAGED_MONEY','OTHER_REPORTABLE','NON_REPORTABLE'))
          OR (report_type='TFF' AND dataset_id='gpe5-46if' AND participant_category IN
                 ('DEALER_INTERMEDIARY','ASSET_MANAGER_INSTITUTIONAL','LEVERAGED_FUNDS','OTHER_REPORTABLE','NON_REPORTABLE'))),
        UNIQUE(instrument,source,dataset_id,report_type,report_scope,market_code,participant_category,mapping_version)
    )""",
    """CREATE TABLE IF NOT EXISTS positioning_ingestions (
        ingestion_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('SUCCESS','PARTIAL','ERROR','UNCHANGED')),
        request_json TEXT NOT NULL CHECK(json_valid(request_json)),
        records_downloaded INTEGER NOT NULL CHECK(records_downloaded>=0),
        records_accepted INTEGER NOT NULL CHECK(records_accepted>=0),
        records_rejected INTEGER NOT NULL CHECK(records_rejected>=0),
        instruments_updated INTEGER NOT NULL CHECK(instruments_updated>=0),
        duration_ms INTEGER NOT NULL CHECK(duration_ms>=0),
        error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
        CHECK(finished_at>=started_at)
    )""",
    """CREATE TABLE IF NOT EXISTS positioning_releases (
        release_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        report_date TEXT NOT NULL,
        scheduled_at TEXT,
        published_at TEXT,
        status TEXT NOT NULL CHECK(status IN ('SCHEDULED','CONFIRMED','DELAYED','NO_REPORT')),
        evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
        calendar_version TEXT NOT NULL REFERENCES positioning_versions(version),
        known_at TEXT NOT NULL,
        CHECK(published_at IS NULL OR status='CONFIRMED'),
        CHECK(published_at IS NULL OR published_at>=report_date)
    )""",
    """CREATE TABLE IF NOT EXISTS positioning_raw_reports (
        raw_report_id INTEGER PRIMARY KEY,
        ingestion_id TEXT NOT NULL REFERENCES positioning_ingestions(ingestion_id),
        source TEXT NOT NULL CHECK(source='CFTC'),
        dataset_id TEXT NOT NULL,
        report_type TEXT NOT NULL CHECK(report_type IN ('LEGACY','DISAGGREGATED','TFF')),
        report_scope TEXT NOT NULL CHECK(report_scope='FUTURES_ONLY'),
        market_code TEXT NOT NULL,
        report_date TEXT NOT NULL,
        release_id TEXT REFERENCES positioning_releases(release_id),
        fetched_at TEXT NOT NULL,
        known_at TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>=1),
        content_hash TEXT NOT NULL,
        raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
        open_interest INTEGER CHECK(open_interest IS NULL OR
                                   (typeof(open_interest)='integer' AND open_interest>=0)),
        CHECK(known_at>=fetched_at AND fetched_at>=report_date),
        CHECK((report_type='LEGACY' AND dataset_id='6dca-aqww') OR
              (report_type='DISAGGREGATED' AND dataset_id='72hh-3qpy') OR
              (report_type='TFF' AND dataset_id='gpe5-46if')),
        UNIQUE(source,dataset_id,report_type,report_scope,market_code,report_date,revision)
    )""",
    """CREATE TABLE IF NOT EXISTS positioning_observations (
        observation_id INTEGER PRIMARY KEY,
        series_id TEXT NOT NULL REFERENCES positioning_series(series_id),
        raw_report_id INTEGER NOT NULL REFERENCES positioning_raw_reports(raw_report_id),
        long_position INTEGER CHECK(long_position IS NULL OR
                                   (typeof(long_position)='integer' AND long_position>=0)),
        short_position INTEGER CHECK(short_position IS NULL OR
                                    (typeof(short_position)='integer' AND short_position>=0)),
        spreading INTEGER CHECK(spreading IS NULL OR (typeof(spreading)='integer' AND spreading>=0)),
        quality_json TEXT NOT NULL CHECK(json_valid(quality_json)),
        UNIQUE(series_id,raw_report_id)
    )""",
    """CREATE TABLE IF NOT EXISTS positioning_metrics (
        snapshot_id TEXT PRIMARY KEY,
        instrument TEXT NOT NULL,
        series_id TEXT REFERENCES positioning_series(series_id),
        observation_id INTEGER REFERENCES positioning_observations(observation_id),
        data_kind TEXT NOT NULL CHECK(data_kind IN ('OBSERVED_CFTC','SYNTHETIC_POSITIONING')),
        report_date TEXT NOT NULL,
        as_of TEXT NOT NULL,
        computed_at TEXT NOT NULL,
        calculation_version TEXT NOT NULL REFERENCES positioning_versions(version),
        availability_version TEXT NOT NULL REFERENCES positioning_versions(version),
        net_position INTEGER,
        normalized_net REAL CHECK(normalized_net IS NULL OR normalized_net BETWEEN -1 AND 1),
        weekly_change INTEGER,
        percentile_52w REAL CHECK(percentile_52w IS NULL OR percentile_52w BETWEEN 0 AND 100),
        percentile_156w REAL CHECK(percentile_156w IS NULL OR percentile_156w BETWEEN 0 AND 100),
        score_52w REAL CHECK(score_52w IS NULL OR score_52w BETWEEN -1 AND 1),
        score_156w REAL CHECK(score_156w IS NULL OR score_156w BETWEEN -1 AND 1),
        positioning_state TEXT CHECK(positioning_state IS NULL OR positioning_state IN
                     ('EXTREME_LONG','LONG','NEUTRAL','SHORT','EXTREME_SHORT')),
        metrics_json TEXT NOT NULL CHECK(json_valid(metrics_json)),
        quality_json TEXT NOT NULL CHECK(json_valid(quality_json)),
        content_hash TEXT NOT NULL,
        CHECK(computed_at>=as_of AND as_of>=report_date),
        CHECK((data_kind='OBSERVED_CFTC' AND series_id IS NOT NULL AND observation_id IS NOT NULL)
           OR (data_kind='SYNTHETIC_POSITIONING' AND observation_id IS NULL AND series_id IS NULL
               AND net_position IS NULL AND normalized_net IS NULL AND weekly_change IS NULL
               AND percentile_52w IS NULL AND percentile_156w IS NULL AND positioning_state IS NULL))
    )""",
    """CREATE TABLE IF NOT EXISTS positioning_metric_inputs (
        input_id INTEGER PRIMARY KEY,
        snapshot_id TEXT NOT NULL REFERENCES positioning_metrics(snapshot_id),
        observation_id INTEGER REFERENCES positioning_observations(observation_id),
        parent_snapshot_id TEXT REFERENCES positioning_metrics(snapshot_id),
        role TEXT NOT NULL CHECK(role IN ('CURRENT','HISTORY','BASE','QUOTE')),
        transform_json TEXT NOT NULL CHECK(json_valid(transform_json)),
        CHECK((observation_id IS NOT NULL AND parent_snapshot_id IS NULL AND role IN ('CURRENT','HISTORY'))
           OR (observation_id IS NULL AND parent_snapshot_id IS NOT NULL AND role IN ('BASE','QUOTE'))),
        CHECK(parent_snapshot_id IS NULL OR snapshot_id<>parent_snapshot_id)
    )""",
    # Generaliza el registro de consultas_biblioteca; no altera su tabla ni función.
    """CREATE TABLE IF NOT EXISTS forecast_context_runs (
        run_id TEXT PRIMARY KEY,
        agent_execution_id INTEGER REFERENCES ejecuciones_agente(id),
        started_at TEXT NOT NULL,
        engine_version TEXT NOT NULL,
        provenance_version TEXT NOT NULL,
        tools_schema_json TEXT NOT NULL CHECK(json_valid(tools_schema_json)),
        tools_schema_hash TEXT NOT NULL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
    )""",
    """CREATE TABLE IF NOT EXISTS tool_invocations (
        invocation_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES forecast_context_runs(run_id),
        tool_name TEXT NOT NULL,
        provider_tool_call_id TEXT NOT NULL,
        response_sequence INTEGER NOT NULL CHECK(response_sequence>=1),
        invoked_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('SUCCESS','ERROR')),
        arguments_json TEXT NOT NULL CHECK(json_valid(arguments_json)),
        result_json TEXT NOT NULL CHECK(json_valid(result_json)),
        data_references_json TEXT NOT NULL CHECK(json_valid(data_references_json)),
        legacy_reference_json TEXT CHECK(legacy_reference_json IS NULL OR json_valid(legacy_reference_json)),
        CHECK(completed_at>=invoked_at),
        UNIQUE(run_id,provider_tool_call_id)
    )""",
    """CREATE TABLE IF NOT EXISTS tool_result_deliveries (
        delivery_id TEXT PRIMARY KEY,
        invocation_id TEXT NOT NULL REFERENCES tool_invocations(invocation_id),
        request_sequence INTEGER NOT NULL CHECK(request_sequence>=1),
        delivered_at TEXT NOT NULL,
        provider_request_id TEXT NOT NULL,
        UNIQUE(invocation_id,request_sequence)
    )""",
    """CREATE TABLE IF NOT EXISTS prediction_contexts (
        prediction_id INTEGER PRIMARY KEY REFERENCES predicciones(id),
        run_id TEXT NOT NULL REFERENCES forecast_context_runs(run_id),
        response_sequence INTEGER NOT NULL CHECK(response_sequence>=1),
        recorded_at TEXT NOT NULL,
        available_as_of TEXT NOT NULL,
        available_data_json TEXT NOT NULL CHECK(json_valid(available_data_json)),
        trace_status TEXT NOT NULL CHECK(trace_status IN ('COMPLETE','INCOMPLETE')),
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json))
    )""",
    """CREATE TABLE IF NOT EXISTS prediction_tool_usage (
        prediction_id INTEGER NOT NULL REFERENCES prediction_contexts(prediction_id),
        invocation_id TEXT NOT NULL REFERENCES tool_invocations(invocation_id),
        delivery_id TEXT NOT NULL REFERENCES tool_result_deliveries(delivery_id),
        associated_at TEXT NOT NULL,
        PRIMARY KEY(prediction_id,invocation_id)
    )""",
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS positioning_series_instrument ON positioning_series(instrument,report_type,participant_category)",
    "CREATE INDEX IF NOT EXISTS positioning_raw_asof ON positioning_raw_reports(dataset_id,market_code,report_date,known_at,revision)",
    "CREATE INDEX IF NOT EXISTS positioning_raw_ingestion ON positioning_raw_reports(ingestion_id)",
    "CREATE INDEX IF NOT EXISTS positioning_metrics_asof ON positioning_metrics(instrument,report_date,as_of,calculation_version)",
    "CREATE INDEX IF NOT EXISTS positioning_metrics_series ON positioning_metrics(series_id,report_date,as_of)",
    "CREATE INDEX IF NOT EXISTS positioning_releases_date ON positioning_releases(source,report_date,known_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS positioning_inputs_observation ON positioning_metric_inputs(snapshot_id,observation_id) WHERE observation_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS positioning_inputs_parent ON positioning_metric_inputs(snapshot_id,role) WHERE parent_snapshot_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS tool_invocations_run ON tool_invocations(run_id,response_sequence)",
    "CREATE INDEX IF NOT EXISTS prediction_contexts_run ON prediction_contexts(run_id)",
)

TRIGGERS = (
    """CREATE TRIGGER IF NOT EXISTS positioning_observation_identity BEFORE INSERT ON positioning_observations
        WHEN NOT EXISTS (
            SELECT 1 FROM positioning_series s JOIN positioning_raw_reports r
            ON s.source=r.source AND s.dataset_id=r.dataset_id AND s.report_type=r.report_type
               AND s.report_scope=r.report_scope AND s.market_code=r.market_code
            WHERE s.series_id=NEW.series_id AND r.raw_report_id=NEW.raw_report_id)
        BEGIN SELECT RAISE(ABORT,'CFTC series/report mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS positioning_metric_identity BEFORE INSERT ON positioning_metrics
        WHEN NEW.data_kind='OBSERVED_CFTC' AND NOT EXISTS (
            SELECT 1 FROM positioning_observations o JOIN positioning_series s USING(series_id)
            JOIN positioning_raw_reports r USING(raw_report_id)
            WHERE o.observation_id=NEW.observation_id AND o.series_id=NEW.series_id
            AND s.instrument=NEW.instrument AND r.report_date=NEW.report_date AND r.known_at<=NEW.as_of)
        BEGIN SELECT RAISE(ABORT,'metric identity or availability mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS tool_delivery_order BEFORE INSERT ON tool_result_deliveries
        WHEN NOT EXISTS (SELECT 1 FROM tool_invocations i WHERE i.invocation_id=NEW.invocation_id
            AND i.response_sequence<NEW.request_sequence AND i.completed_at<=NEW.delivered_at)
        BEGIN SELECT RAISE(ABORT,'tool result not available for this request'); END""",
    """CREATE TRIGGER IF NOT EXISTS positioning_input_cutoff BEFORE INSERT ON positioning_metric_inputs
        WHEN NOT EXISTS (
            SELECT 1 FROM positioning_metrics m WHERE m.snapshot_id=NEW.snapshot_id AND (
                (NEW.observation_id IS NOT NULL AND m.data_kind='OBSERVED_CFTC' AND EXISTS (
                    SELECT 1 FROM positioning_observations o JOIN positioning_raw_reports r USING(raw_report_id)
                    WHERE o.observation_id=NEW.observation_id AND o.series_id=m.series_id
                    AND r.known_at<=m.as_of AND r.report_date<=m.report_date))
                OR (NEW.parent_snapshot_id IS NOT NULL AND m.data_kind='SYNTHETIC_POSITIONING' AND EXISTS (
                    SELECT 1 FROM positioning_metrics parent WHERE parent.snapshot_id=NEW.parent_snapshot_id
                    AND parent.data_kind='OBSERVED_CFTC' AND parent.report_date=m.report_date
                    AND parent.calculation_version=m.calculation_version AND parent.as_of<=m.as_of))))
        BEGIN SELECT RAISE(ABORT,'input series, methodology or availability mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS prediction_tool_usage_order BEFORE INSERT ON prediction_tool_usage
        WHEN NOT EXISTS (
            SELECT 1 FROM prediction_contexts p JOIN tool_invocations i ON p.run_id=i.run_id
            JOIN tool_result_deliveries d ON d.invocation_id=i.invocation_id
            WHERE p.prediction_id=NEW.prediction_id AND i.invocation_id=NEW.invocation_id
            AND d.delivery_id=NEW.delivery_id AND d.request_sequence<=p.response_sequence
            AND i.response_sequence<p.response_sequence)
        BEGIN SELECT RAISE(ABORT,'tool result was not delivered before forecast reasoning'); END""",
    """CREATE TRIGGER IF NOT EXISTS prediction_context_cutoff BEFORE INSERT ON prediction_contexts
        WHEN NOT EXISTS (SELECT 1 FROM predicciones p WHERE p.id=NEW.prediction_id
            AND julianday(NEW.available_as_of)<=julianday(p.creado)
            AND julianday(NEW.recorded_at)>=julianday(p.creado))
        BEGIN SELECT RAISE(ABORT,'forecast context cutoff invalid'); END""",
    """CREATE TRIGGER IF NOT EXISTS prediction_tool_usage_time BEFORE INSERT ON prediction_tool_usage
        WHEN NOT EXISTS (
            SELECT 1 FROM predicciones p JOIN tool_result_deliveries d
            WHERE p.id=NEW.prediction_id AND d.delivery_id=NEW.delivery_id
            AND julianday(d.delivered_at)<=julianday(p.creado)
            AND julianday(NEW.associated_at)>=julianday(p.creado))
        BEGIN SELECT RAISE(ABORT,'tool delivery happened after forecast creation'); END""",
)


def register_version(c, version, kind, specification):
    """Repetir una versión idéntica es inocuo; cambiar su significado falla."""
    encoded, digest = canonical_json(specification), content_hash(specification)
    row = c.execute("SELECT kind,specification_json,specification_hash FROM positioning_versions WHERE version=?",
                    (version,)).fetchone()
    if row is not None:
        if tuple(row) != (kind, encoded, digest):
            raise ValueError(f"No se puede redefinir la versión {version}")
        return
    c.execute("INSERT INTO positioning_versions VALUES (?,?,?,?,?)",
              (version, kind, encoded, digest, datetime.now(timezone.utc).isoformat(timespec="microseconds")))


def migrate(c):
    """SAVEPOINT respeta la transacción del llamante; no usa executescript/commit."""
    c.execute("SAVEPOINT positioning_schema_migration")
    try:
        for statement in TABLES + INDEXES + TRIGGERS:
            c.execute(statement)
        for statement in TABLES:
            name = statement.split()[5]
            for operation in ("UPDATE", "DELETE"):
                c.execute(f"""CREATE TRIGGER IF NOT EXISTS {name}_immutable_{operation.lower()}
                    BEFORE {operation} ON {name}
                    BEGIN SELECT RAISE(ABORT,'append-only positioning/provenance'); END""")
        register_version(c, SCHEMA_VERSION, "SCHEMA", {
            "version": SCHEMA_VERSION, "ddl_hash": content_hash(TABLES + INDEXES + TRIGGERS),
            "storage": "append-only raw, derived and external forecast context",
        })
        register_version(c, AVAILABILITY_VERSION, "AVAILABILITY", AVAILABILITY_POLICY)
    except Exception:
        c.execute("ROLLBACK TO positioning_schema_migration")
        c.execute("RELEASE positioning_schema_migration")
        raise
    c.execute("RELEASE positioning_schema_migration")
