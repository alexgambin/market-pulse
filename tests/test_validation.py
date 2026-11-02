import ast
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import db
import memoria
import validation as v
from config import CAMPOS_INMUTABLES

NOW = datetime(2026, 9, 8, 14, tzinfo=timezone.utc)


def prediction(**changes):
    p = dict(instrumento='Oro', condicion='>=', nivel_objetivo=100, modo='toca',
             fecha_limite='2026-09-05', creado='2026-09-01T12:00:00+00:00',
             schema_version=2, specification_source='original', probabilidad=.2)
    return {**p, **changes}


def bar(day='2026-09-02', high=101, low=90, close=95, **extra):
    return dict(fecha=day, high=high, low=low, close=close, **extra)


class PriceTests(unittest.TestCase):
    def evaluate(self, bars=None, **changes):
        return v.evaluar(prediction(**changes), lambda *args: bars if bars is not None else [bar()], NOW)

    def test_touch_true_and_event_date(self):
        r = self.evaluate()
        self.assertEqual((r['estado_validacion'], r['resultado'], r['fecha_resolucion']), ('RESOLVED', 1, '2026-09-02'))

    def test_touch_expired_false(self):
        self.assertEqual(self.evaluate([bar(high=99)])['resultado'], 0)

    def test_operator_boundaries(self):
        for op, expected in [('>=',1), ('>',0), ('<=',1), ('<',0)]:
            with self.subTest(op=op):
                self.assertEqual(self.evaluate([bar(high=100, low=100, close=100)], condicion=op)['resultado'], expected)

    def test_no_data(self):
        r = self.evaluate([])
        self.assertEqual((r['estado_validacion'], r['resultado']), ('DATA_ERROR', None))

    def test_pending(self):
        self.assertEqual(self.evaluate([bar(high=99)], fecha_limite='2026-09-30')['estado_validacion'], 'PENDING')

    def test_no_creation_day_touch(self):
        self.assertEqual(self.evaluate([bar('2026-09-01'), bar(high=99)])['resultado'], 0)

    def test_touch_early(self):
        self.assertEqual(self.evaluate(fecha_limite='2026-09-30')['resultado'], 1)

    def test_close_never_early(self):
        r = self.evaluate([bar(close=101)], modo='cierra', fecha_limite='2026-09-30')
        self.assertEqual((r['estado_validacion'], r['resultado']), ('PENDING', None))

    def test_close_only_final_with_unsorted_outside_data(self):
        r = self.evaluate([bar('2026-09-06', close=101), bar('2026-09-05'), bar(close=101)], modo='cierra')
        self.assertEqual((r['resultado'], r['fecha_resolucion']), (0, '2026-09-05'))

    def test_close_skips_creation_day(self):
        self.assertEqual(self.evaluate([bar('2026-09-01', close=101)], modo='cierra')['estado_validacion'], 'DATA_ERROR')

    def test_weekend_deadline_uses_friday_not_next_session(self):
        for deadline in ('2026-09-05', '2026-09-06'):
            with self.subTest(deadline=deadline):
                r = self.evaluate([bar('2026-09-03', close=95),
                                   bar('2026-09-04', close=101),
                                   bar('2026-09-07', close=95)],
                                  instrumento='USD/JPY', modo='cierra', fecha_limite=deadline)
                self.assertEqual((r['resultado'], r['fecha_resolucion']), (1, '2026-09-04'))

    def test_market_holiday_fixture_uses_previous_session(self):
        # Fixture: el proveedor entrega viernes y martes, lunes sin sesión.
        # No pretende validar un calendario externo ni certificar su integridad.
        r = self.evaluate([bar('2026-09-03', close=101),
                           bar('2026-09-04', close=95),
                           bar('2026-09-08', close=101)],
                          instrumento='S&P 500', modo='cierra', fecha_limite='2026-09-07')
        self.assertEqual((r['resultado'], r['fecha_resolucion']), (0, '2026-09-04'))

    def test_crypto_weekend_session_is_not_replaced_by_friday(self):
        r = self.evaluate([bar('2026-09-04', close=101), bar('2026-09-05', close=95)],
                          instrumento='ETH', modo='cierra', fecha_limite='2026-09-05')
        self.assertEqual((r['resultado'], r['fecha_resolucion']), (0, '2026-09-05'))

    def test_touch_can_succeed_while_close_fails_on_same_bar(self):
        bars = [bar('2026-09-04', high=160.052, low=159.297, close=159.321)]
        common = dict(instrumento='USD/JPY', nivel_objetivo=160, condicion='>=')
        self.assertEqual(self.evaluate(bars, modo='toca', **common)['resultado'], 1)
        self.assertEqual(self.evaluate(bars, modo='cierra', **common)['resultado'], 0)

    def test_zero_target(self):
        p = prediction(nivel_objetivo=0)
        self.assertTrue(v.resoluble_por_precio(p))
        self.assertEqual(self.evaluate([bar(high=0, low=-1, close=0)], nivel_objetivo=0)['resultado'], 1)

    def test_maintains_review(self):
        self.assertEqual(self.evaluate(modo='mantiene')['estado_validacion'], 'NEEDS_REVIEW')

    def test_unsupported_instrument(self):
        self.assertEqual(self.evaluate(instrumento='NO_EXISTE')['estado_validacion'], 'NEEDS_REVIEW')

    def test_provider_failure(self):
        def fail(*args): raise RuntimeError('provider')
        r = v.evaluar(prediction(), fail, NOW)
        self.assertEqual((r['estado_validacion'], r['resultado']), ('DATA_ERROR', None))

    def test_type_never_controls_validation(self):
        for tipo in ['evento', 'nivel', 'continuidad', None]:
            self.assertEqual(self.evaluate(tipo=tipo)['resultado'], 1)

    def test_legacy_not_inferred(self):
        self.assertEqual(self.evaluate(schema_version=1, specification_source='legacy')['estado_validacion'], 'NEEDS_REVIEW')

    def test_nonfinite_and_invalid_bars(self):
        for b in [bar(high=float('nan')), bar(high=float('inf')), bar(low=150), {}]:
            self.assertEqual(self.evaluate([b])['estado_validacion'], 'DATA_ERROR')

    def test_intraday_deadline_requires_proof(self):
        self.assertEqual(self.evaluate(fecha_limite='2026-09-05T12:00:00+00:00')['estado_validacion'], 'DATA_ERROR')

    def test_session_started_before_prediction_is_discarded(self):
        r = self.evaluate([bar(inicio='2026-09-01T00:00:00+00:00')])
        self.assertEqual(r['estado_validacion'], 'DATA_ERROR')

    def test_future_or_incomplete_bar_discarded(self):
        for b in [bar('2026-09-08'), bar(fin='2026-09-09T00:00:00+00:00')]:
            self.assertEqual(self.evaluate([b], fecha_limite='2026-09-30')['estado_validacion'], 'DATA_ERROR')

    def test_new_prediction_before_first_complete_bar(self):
        r = self.evaluate([], creado=NOW.isoformat(), fecha_limite='2026-09-30')
        self.assertEqual(r['estado_validacion'], 'PENDING')

    def test_duplicate_sessions_are_error(self):
        self.assertEqual(self.evaluate([bar(), bar()])['estado_validacion'], 'DATA_ERROR')


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = str(Path(self.tmp.name) / 'test.db')
        for module in (memoria, db):
            mock = patch.object(module, 'DB_PATH', path)
            mock.start(); self.addCleanup(mock.stop)
        memoria.init_memoria()

    def register(self, **changes):
        args = dict(afirmacion='El evento', probabilidad=.2, horizonte='2026-09-05',
                    instrumento='Oro', nivel_objetivo=100, condicion='>=', modo='toca',
                    fecha_limite='2026-09-05', provider='test', model='model',
                    model_snapshot='snapshot', prompt_version='v2')
        with patch.object(memoria, '_ahora', return_value='2026-09-01T12:00:00+00:00'):
            return memoria.registrar_prediccion(**{**args, **changes})['prediccion_id']

    def test_idempotence_and_timestamps(self):
        ident = self.register()
        for _ in range(2): v.ejecutar_validacion(NOW, lambda *args: [bar()])
        with db._conn() as c:
            r = c.execute('SELECT * FROM predicciones WHERE id=?', (ident,)).fetchone()
            self.assertEqual(r['resultado'], 1)
            self.assertAlmostEqual(r['brier'], .64)
            self.assertEqual(r['fecha_resolucion'], '2026-09-02')
            self.assertNotEqual(r['resuelto'], r['fecha_resolucion'])
            self.assertEqual(c.execute('SELECT count(*) FROM validacion_auditoria').fetchone()[0], 1)

    def test_all_contract_fields_immutable_including_null(self):
        self.register(modo='mantiene', instrumento=None)
        for field in CAMPOS_INMUTABLES:
            with self.subTest(field=field), self.assertRaises(sqlite3.IntegrityError), db._conn() as c:
                c.execute(f'UPDATE predicciones SET {field}=?', ('changed',))

    def test_agent_resolution_blocked(self):
        ident = self.register()
        self.assertFalse(memoria.resolver_prediccion(ident, 1)['ok'])

    def test_manual_zero_good_brier_and_filters(self):
        ident = self.register(modo="mantiene")
        v.ejecutar_validacion(NOW)
        r = v.resolver_manual(ident, 0, 'Fuente verificable', '2026-09-05')
        self.assertTrue(r['ok']); self.assertAlmostEqual(r['brier'], .04)
        self.assertEqual(memoria.calibracion(schema_version=2, resuelto_por='manual', specification_source='original')['n'], 1)
        self.assertEqual(memoria.calibracion(schema_version=1), {'n': 0})
        self.assertEqual(memoria.calibracion_detalle(schema_version=1)['n'], 0)
        self.assertEqual(len(memoria.predicciones_resueltas(resuelto_por='manual')), 1)

    def test_manual_no_result_remains_unscored_and_preserved(self):
        ident = self.register(modo='mantiene')
        v.ejecutar_validacion(NOW)
        self.assertTrue(v.resolver_manual(ident, None, 'No verificable')['ok'])
        self.assertEqual(v.ejecutar_validacion(NOW, lambda *args: [bar()]), {})
        with db._conn() as c:
            r = c.execute('SELECT resultado,brier,resuelto FROM predicciones').fetchone()
            self.assertEqual(tuple(r), (None, None, None))

    def test_invalid_manual_outcomes(self):
        ident = self.register()
        for value in [-1, 2, '1', True, .5]:
            self.assertFalse(v.resolver_manual(ident, value, 'Fuente', '2026-09-05')['ok'])
        self.assertFalse(v.resolver_manual(ident, 1, '')['ok'])
        self.assertFalse(v.resolver_manual(ident, 1, 'Fuente')['ok'])

    def test_database_rejects_sentinels(self):
        self.register()
        for value in [-1, 'ERROR', 2]:
            with self.assertRaises(sqlite3.IntegrityError), db._conn() as c:
                c.execute('UPDATE predicciones SET resultado=?', (value,))

    def test_migration_can_run_again_without_reclassifying_v2(self):
        ident = self.register(modo="mantiene")
        v.ejecutar_validacion(NOW)
        v.resolver_manual(ident, 0, 'Fuente', '2026-09-05')
        memoria.init_memoria()
        self.assertEqual(memoria.calibracion(schema_version=2, resuelto_por='manual')['n'], 1)

    def test_retry_data_error_then_resolve(self):
        self.register()
        self.assertEqual(v.ejecutar_validacion(NOW, lambda *args: []), {'DATA_ERROR': 1})
        self.assertEqual(v.ejecutar_validacion(NOW, lambda *args: [bar()]), {'RESOLVED': 1})

    def test_manual_cannot_hide_data_error(self):
        ident = self.register()
        v.ejecutar_validacion(NOW, lambda *args: [])
        self.assertFalse(v.resolver_manual(ident, None, 'No verificable')['ok'])
        self.assertEqual(len(v.cola_revision()['DATA_ERROR']), 1)

    def test_sql_insert_without_provenance_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError), db._conn() as c:
            c.execute("INSERT INTO predicciones (afirmacion,probabilidad,horizonte,creado) VALUES ('x',.5,'2026-09-05','2026-09-01')")

    def test_concurrent_resolvers_only_one_writes(self):
        from concurrent.futures import ThreadPoolExecutor
        ident = self.register()
        e = v.evaluar(prediction(), lambda *args: [bar()], NOW)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: v.guardar_evaluacion(ident, e), range(2)))
        self.assertEqual(sum(r['ok'] for r in results), 1)
        with db._conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM validacion_auditoria').fetchone()[0], 1)

    def test_maintains_stored_without_relabeling(self):
        self.register(modo='mantiene')
        self.assertEqual(v.ejecutar_validacion(NOW), {'NEEDS_REVIEW': 1})
        self.assertEqual(v.cola_revision()['NEEDS_REVIEW'][0]['modo'], 'mantiene')


class MigrationTests(unittest.TestCase):
    def test_old_schema_preserved_and_marked(self):
        from migrations import migrate_predictions
        with closing(sqlite3.connect(':memory:')) as c:
            c.execute('''CREATE TABLE predicciones(id INTEGER PRIMARY KEY, hilo_id INTEGER,
                afirmacion TEXT, probabilidad REAL, horizonte TEXT, creado TEXT,
                resuelto TEXT, resultado INTEGER, brier REAL)''')
            c.execute("INSERT INTO predicciones VALUES(1,NULL,'legacy',.2,'2026-08-01','2026-07-01', '2026-08-02',0,.04)")
            c.execute("INSERT INTO predicciones VALUES(2,NULL,'legacy open',.2,'2026-12-01','2026-07-01',NULL,NULL,NULL)")
            c.commit(); migrate_predictions(c); c.commit(); migrate_predictions(c)
            rows = c.execute('SELECT schema_version,resuelto_por,resultado,brier,condicion,modo,estado_validacion FROM predicciones ORDER BY id').fetchall()
            self.assertEqual(rows[0], (1,'agente',0,.04,None,None,'RESOLVED'))
            self.assertEqual(rows[1], (1,None,None,None,None,None,'NEEDS_REVIEW'))

    def test_validation_has_no_ai_imports(self):
        for file in ['validation.py', 'db.py', 'precios.py', 'config.py']:
            tree = ast.parse(Path(file).read_text())
            for node in ast.walk(tree):
                names = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(set(names) & {'agente','anthropic','openai'})


if __name__ == '__main__':
    unittest.main()
