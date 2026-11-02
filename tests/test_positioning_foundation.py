"""Fase 1: migración, identidades y aislamiento real del payload del baseline.

Sin descargas. Los valores de métricas son fixtures para probar persistencia,
no resultados ni una aprobación de la fórmula pendiente de Fase 2.
"""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from positioning.contracts import (AVAILABILITY_VERSION, SeriesIdentity, canonical_json,
                                   utc_timestamp)
from positioning import schema


STAMP = "2026-09-09T12:00:00.000000+00:00"
LATER = "2026-09-09T13:00:00.000000+00:00"


class IdentityTests(unittest.TestCase):
    def test_participant_identity_is_family_specific(self):
        a = SeriesIdentity("WTI", "LEGACY", "NON_COMMERCIAL", "067651", "map-v1")
        b = SeriesIdentity("WTI", "DISAGGREGATED", "MANAGED_MONEY", "067651", "map-v1")
        self.assertNotEqual(a.series_id, b.series_id)
        self.assertEqual(a.market_code, "067651")
        self.assertEqual(a.dataset_id, "6dca-aqww")
        for family, participant in [("LEGACY", "MANAGED_MONEY"), ("TFF", "NON_COMMERCIAL"),
                                    ("DISAGGREGATED", "LEVERAGED_FUNDS")]:
            with self.subTest(family=family), self.assertRaises(ValueError):
                SeriesIdentity("WTI", family, participant, "067651", "map-v1")

    def test_price_universe_is_not_expanded(self):
        import precios
        a = SeriesIdentity("WTI", "LEGACY", "NON_COMMERCIAL", "067651", "map-v1")
        a.validate_price_universe(precios.INSTRUMENTOS)
        for name in ("Wheat", "Gold", "NASDAQ 100", "Cattle"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                replace(a, instrument=name).validate_price_universe(precios.INSTRUMENTOS)

    def test_contract_codes_and_mapping_revisions_are_not_collapsed(self):
        a = SeriesIdentity("S&P 500", "LEGACY", "NON_COMMERCIAL", "13874+", "map-v1")
        self.assertEqual(a.market_code, "13874+")
        self.assertNotEqual(a.series_id, replace(a, mapping_version="map-v2").series_id)
        with self.assertRaises(ValueError):
            replace(a, market_code=13874)

    def test_json_rejects_nonfinite_values_and_time_requires_zone(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.assertRaises(ValueError):
                canonical_json({"value": value})
        with self.assertRaises(ValueError):
            utc_timestamp(datetime(2026, 9, 9))
        self.assertEqual(utc_timestamp(datetime(2026, 9, 9, 12, tzinfo=timezone.utc)), STAMP)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.addCleanup(self.c.close)
        self.c.execute("PRAGMA foreign_keys=ON")
        self.c.execute("CREATE TABLE predicciones (id INTEGER PRIMARY KEY, creado TEXT, frozen TEXT)")
        self.c.execute("CREATE TABLE ejecuciones_agente (id INTEGER PRIMARY KEY)")
        self.c.execute("INSERT INTO predicciones VALUES (1,?,'intacto')", (LATER,))
        self.c.commit()
        schema.migrate(self.c)

    def insert(self, table, **values):
        keys = ','.join(values)
        params = ','.join('?' for _ in values)
        return self.c.execute(f"INSERT INTO {table} ({keys}) VALUES ({params})", tuple(values.values()))

    def seed(self):
        schema.register_version(self.c, "mapping-test", "MAPPING", {"fixture": True})
        schema.register_version(self.c, "calc-test", "CALCULATION", {"fixture": True})
        self.insert("positioning_series", series_id="s1", instrument="WTI", source="CFTC",
                    dataset_id="6dca-aqww", report_type="LEGACY", report_scope="FUTURES_ONLY",
                    market_code="067651", participant_category="NON_COMMERCIAL",
                    mapping_version="mapping-test", source_name="WTI fixture", price_symbol="CL=F",
                    relationship="UNDERLYING", unit="CONTRACTS", validation_evidence_json='{}',
                    valid_from="2016-01-01", created_at=STAMP)
        self.insert("positioning_ingestions", ingestion_id="i1", source="CFTC", started_at=STAMP,
                    finished_at=STAMP, status="SUCCESS", request_json='{}', records_downloaded=1,
                    records_accepted=1, records_rejected=0, instruments_updated=1, duration_ms=0)
        self.raw()
        self.insert("positioning_observations", observation_id=1, series_id="s1", raw_report_id=1,
                    long_position=100, short_position=40, quality_json='{}')

    def raw(self, **changes):
        values = dict(raw_report_id=1, ingestion_id="i1", source="CFTC", dataset_id="6dca-aqww",
                      report_type="LEGACY", report_scope="FUTURES_ONLY", market_code="067651",
                      report_date="2026-09-01", fetched_at=STAMP, known_at=STAMP, revision=1,
                      content_hash="hash-A", raw_json='{"fixture":true}', open_interest=200)
        return self.insert("positioning_raw_reports", **{**values, **changes})

    def metric(self, **changes):
        values = dict(snapshot_id="m1", instrument="WTI", series_id="s1", observation_id=1,
                      data_kind="OBSERVED_CFTC", report_date="2026-09-01", as_of=STAMP,
                      computed_at=STAMP, calculation_version="calc-test",
                      availability_version=AVAILABILITY_VERSION, net_position=60, normalized_net=.3,
                      percentile_52w=None, percentile_156w=None, metrics_json='{}',
                      quality_json='{"reason":"INSUFFICIENT_HISTORY"}', content_hash="hash-m1")
        return self.insert("positioning_metrics", **{**values, **changes})

    def seed_run(self, run_id="run1"):
        self.insert("forecast_context_runs", run_id=run_id, started_at=STAMP,
                    engine_version="price-daily-v1", provenance_version="tool-context-test",
                    tools_schema_json='[]', tools_schema_hash="empty", metadata_json='{}')

    def invocation(self, invocation_id="call1", run_id="run1", response_sequence=1):
        self.insert("tool_invocations", invocation_id=invocation_id, run_id=run_id,
                    tool_name="consultar_posicionamiento", provider_tool_call_id=invocation_id,
                    response_sequence=response_sequence, invoked_at=STAMP, completed_at=STAMP,
                    status="SUCCESS", arguments_json='{"instrument":"WTI"}',
                    result_json='{"snapshot_id":"fixture"}', data_references_json='[]')

    def delivery(self, invocation_id="call1", request_sequence=2):
        self.insert("tool_result_deliveries", delivery_id="delivery-"+invocation_id,
                    invocation_id=invocation_id, request_sequence=request_sequence,
                    delivered_at=STAMP, provider_request_id="request-fixture")

    def context(self, **changes):
        values = dict(prediction_id=1, run_id="run1", response_sequence=2,
                      recorded_at=LATER, available_as_of=STAMP, available_data_json='[]',
                      trace_status="COMPLETE", metadata_json='{}')
        self.insert("prediction_contexts", **{**values, **changes})

    def usage(self, invocation_id="call1"):
        self.insert("prediction_tool_usage", prediction_id=1, invocation_id=invocation_id,
                    delivery_id="delivery-"+invocation_id, associated_at=LATER)

    def test_migration_idempotent_preserves_existing_schema_and_rows(self):
        self.seed()
        before = list(self.c.iterdump())
        schema.migrate(self.c)
        schema.migrate(self.c)
        self.assertEqual(before, list(self.c.iterdump()))
        self.assertEqual(self.c.execute("SELECT frozen FROM predicciones").fetchone()[0], "intacto")
        self.assertEqual(self.c.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_migration_is_atomic_and_does_not_commit_outer_transaction(self):
        with sqlite3.connect(":memory:") as other:
            other.execute("CREATE TABLE existing (value TEXT)")
            other.execute("INSERT INTO existing VALUES ('outer')")
            with patch.object(schema, 'register_version', side_effect=RuntimeError('test failure')):
                with self.assertRaises(RuntimeError):
                    schema.migrate(other)
            self.assertIsNone(other.execute("SELECT name FROM sqlite_master WHERE name='positioning_versions'").fetchone())
            other.rollback()
            self.assertEqual(other.execute("SELECT * FROM existing").fetchall(), [])

    def test_successful_migration_does_not_commit_outer_transaction(self):
        with sqlite3.connect(":memory:") as other:
            other.execute("BEGIN")
            schema.migrate(other)
            other.rollback()
            self.assertEqual(other.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_versions_cannot_change_meaning_and_calculation_is_not_activated(self):
        schema.register_version(self.c, "fixture-v1", "CALCULATION", {"minimum": 52})
        schema.register_version(self.c, "fixture-v1", "CALCULATION", {"minimum": 52})
        with self.assertRaises(ValueError):
            schema.register_version(self.c, "fixture-v1", "CALCULATION", {"minimum": 50})
        rows = self.c.execute("SELECT version FROM positioning_versions WHERE kind='CALCULATION'").fetchall()
        self.assertEqual(rows, [("fixture-v1",)])

    def test_prior_schema_revision_is_preserved(self):
        schema.register_version(self.c, "positioning-schema-v1", "SCHEMA", {"prior": True})
        before = self.c.execute("SELECT * FROM positioning_versions WHERE version='positioning-schema-v1'").fetchone()
        schema.migrate(self.c)
        self.assertEqual(before, self.c.execute("SELECT * FROM positioning_versions WHERE version='positioning-schema-v1'").fetchone())
        self.assertIsNotNone(self.c.execute("SELECT name FROM sqlite_master WHERE name='positioning_input_cutoff'").fetchone())

    def test_null_is_not_zero_and_both_percentiles_persist(self):
        self.seed()
        self.metric()
        self.assertEqual(self.c.execute("SELECT percentile_52w,percentile_156w,positioning_state FROM positioning_metrics").fetchone(), (None,None,None))
        self.metric(snapshot_id="m2", percentile_52w=90, percentile_156w=60)
        self.assertEqual(self.c.execute("SELECT percentile_52w,percentile_156w FROM positioning_metrics WHERE snapshot_id='m2'").fetchone(), (90,60))
        self.raw(raw_report_id=2, revision=2, open_interest=None)
        self.assertIsNone(self.c.execute("SELECT open_interest FROM positioning_raw_reports WHERE raw_report_id=2").fetchone()[0])

    def test_append_only_and_revisions_allow_a_b_a(self):
        self.seed()
        self.raw(raw_report_id=2, revision=2, content_hash="hash-B")
        self.raw(raw_report_id=3, revision=3, content_hash="hash-A")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw(raw_report_id=4, revision=3)
        for sql in ("DELETE FROM positioning_raw_reports", "UPDATE positioning_raw_reports SET open_interest=10",
                    "UPDATE positioning_series SET market_code='bad'", "DELETE FROM positioning_versions"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.c.execute(sql)

    def test_raw_contract_mismatch_rejected(self):
        self.seed()
        self.raw(raw_report_id=2, market_code="088691")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("positioning_observations", series_id="s1", raw_report_id=2, quality_json='{}')

    def test_invalid_report_family_dataset_rejected(self):
        self.seed()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw(raw_report_id=2, report_type="TFF")

    def test_metric_cannot_use_data_received_after_cutoff(self):
        self.seed()
        with self.assertRaises(sqlite3.IntegrityError):
            self.metric(as_of="2026-09-08T12:00:00.000000+00:00")

    def test_history_lineage_rejects_future_vintage(self):
        self.seed()
        self.metric()
        self.raw(raw_report_id=2, revision=2, fetched_at=LATER, known_at=LATER)
        self.insert("positioning_observations", observation_id=2, series_id="s1", raw_report_id=2, quality_json='{}')
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("positioning_metric_inputs", snapshot_id="m1", observation_id=2,
                        role="HISTORY", transform_json='{}')
        self.insert("positioning_metric_inputs", snapshot_id="m1", observation_id=1,
                    role="CURRENT", transform_json='{}')

    def test_synthetic_has_no_fake_contract_net_or_classification(self):
        self.seed()
        changes = dict(data_kind="SYNTHETIC_POSITIONING", series_id=None, observation_id=None,
                       net_position=None, normalized_net=None)
        self.metric(**changes)
        for invalid in (dict(net_position=60), dict(positioning_state="EXTREME_LONG")):
            with self.subTest(invalid=invalid), self.assertRaises(sqlite3.IntegrityError):
                self.metric(**{**changes, "snapshot_id": "bad", **invalid})

    def test_report_publication_and_fetch_remain_distinct(self):
        self.seed()
        schema.register_version(self.c,"calendar-test","CALENDAR",{"fixture":True})
        self.insert("positioning_releases", release_id="r1", source="CFTC", report_date="2026-09-01",
                    scheduled_at="2026-09-04T19:30:00.000000+00:00", published_at=None,
                    status="DELAYED", evidence_json='{}', calendar_version="calendar-test", known_at=STAMP)
        row = self.c.execute("SELECT report_date,scheduled_at,published_at FROM positioning_releases").fetchone()
        self.assertEqual(row, ("2026-09-01","2026-09-04T19:30:00.000000+00:00",None))

    def test_multiple_delivered_calls_can_link_one_prediction(self):
        self.seed_run()
        self.context()
        for call_id in ("call1","call2"):
            self.invocation(call_id)
            self.delivery(call_id)
            self.usage(call_id)
        self.assertEqual(self.c.execute("SELECT count(*) FROM prediction_tool_usage").fetchone()[0],2)
        self.assertEqual(self.c.execute("SELECT frozen FROM predicciones").fetchone()[0], "intacto")

    def test_same_response_prediction_cannot_have_read_tool_result(self):
        self.seed_run()
        self.invocation()
        self.delivery()
        self.context(response_sequence=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.usage()

    def test_delivery_must_follow_invocation_response(self):
        self.seed_run()
        self.invocation()
        with self.assertRaises(sqlite3.IntegrityError):
            self.delivery(request_sequence=1)

    def test_tool_usage_cannot_leak_across_executions(self):
        self.seed_run()
        self.seed_run("other")
        self.invocation(run_id="other")
        self.delivery()
        self.context()
        with self.assertRaises(sqlite3.IntegrityError):
            self.usage()

    def test_delivery_after_forecast_is_not_usage_even_with_matching_sequence(self):
        self.seed_run()
        self.invocation()
        self.insert("tool_result_deliveries", delivery_id="delivery-call1", invocation_id="call1",
                    request_sequence=2, delivered_at="2026-09-10T12:00:00.000000+00:00",
                    provider_request_id="too-late")
        self.context()
        with self.assertRaises(sqlite3.IntegrityError):
            self.usage()

    def test_available_cutoff_cannot_follow_prediction(self):
        self.seed_run()
        with self.assertRaises(sqlite3.IntegrityError):
            self.context(available_as_of="2026-09-10T00:00:00.000000+00:00")


class BaselinePayloadTests(unittest.TestCase):
    def test_flag_off_actual_model_request_contains_exact_baseline_tools(self):
        import agente
        import config
        import derivados
        import onchain
        fixture = json.loads(Path("tests/fixtures/positioning_baseline_tools.json").read_text())
        response = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text",text="intacto")])
        create = AsyncMock(return_value=response)
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        news = [{"id":1,"fuente":"fixture","titular":"WTI fixture","resumen":"offline"}]
        captured = []

        async def forward(call, context, **kwargs):
            captured.append(kwargs)
            return await call(**kwargs)

        with ExitStack() as stack:
            self.assertIs(config.POSITIONING_AGENT_ENABLED, False)
            stack.enter_context(patch.object(config, 'POSITIONING_AGENT_ENABLED', False))
            stack.enter_context(patch.object(agente.anthropic, 'AsyncAnthropic', return_value=client))
            stack.enter_context(patch.object(agente, '_triaje', AsyncMock(return_value=news)))
            stack.enter_context(patch.object(agente.consumo_ia, 'llamar', side_effect=forward))
            stack.enter_context(patch.object(agente, 'cargar_doctrina', return_value=''))
            stack.enter_context(patch.object(agente.memoria, 'predicciones_abiertas', return_value=[]))
            stack.enter_context(patch.object(agente.memoria, 'calibracion', return_value={}))
            for module, method in [(agente.precios,'cinta'), (agente.cot,'resumen'),
                                   (agente.calendario,'proximos'), (agente.fred,'macro'),
                                   (agente.eia,'energia'), (onchain,'resumen'), (derivados,'resumen')]:
                stack.enter_context(patch.object(module,method,AsyncMock(return_value='BASELINE:'+method)))
            result = asyncio.run(agente.ejecutar_agente("fixture", news))
        self.assertEqual(result, "intacto")
        create.assert_awaited_once()
        self.assertEqual(captured[0]['tools'], fixture)
        self.assertNotIn("consultar_posicionamiento", [t['name'] for t in captured[0]['tools']])
        self.assertIn('BASELINE:resumen', captured[0]['messages'][0]['content'])
        self.assertEqual(captured[0]['system'][0]['text'], agente.SISTEMA_AGENTE)


if __name__ == "__main__":
    unittest.main()
