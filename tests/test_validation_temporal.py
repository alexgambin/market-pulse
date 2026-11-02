"""Espera por margen temporal: fixtures offline, sin cambiar las velas admitidas."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import db
import memoria
import validation as v

NOW = datetime(2026, 9, 12, 6, tzinfo=timezone.utc)
PREDICTION = dict(
    instrumento='WTI', condicion='>=', nivel_objetivo=100, modo='toca',
    fecha_limite='2026-09-22', creado='2026-09-10T08:05:16+00:00',
    schema_version=2, specification_source='original', probabilidad=.35,
)
BAR = dict(
    fecha='2026-09-11', inicio='2026-09-10T00:00:00-04:00',
    fin='2026-09-12T00:00:00-04:00', high=104.46, low=98.48, close=100.05,
)


class TemporalAvailabilityTests(unittest.TestCase):
    def evaluate(self, bars, **changes):
        return v.evaluar({**PREDICTION, **changes}, lambda *args: bars, NOW)

    def test_overlap_is_pending_without_admitting_price(self):
        result = self.evaluate([BAR])
        self.assertEqual(result['estado_validacion'], 'PENDING')
        self.assertIsNone(result['resultado'])
        self.assertEqual(result['evidencia']['barras'], [])
        diagnostic = result['evidencia']['diagnostico_temporal']
        self.assertEqual(diagnostic['version'], 'data-availability-v1')
        self.assertEqual(diagnostic['descartadas'][0]['motivo'], 'INTERVALO_SOLAPA_CREACION')

    def test_future_finish_preserves_existing_error_state(self):
        result = self.evaluate([{**BAR, 'inicio': '2026-09-11T00:00:00Z',
                                'fin': '2026-09-13T00:00:00Z'}])
        self.assertEqual(result['estado_validacion'], 'DATA_ERROR')
        self.assertIsNone(result['resultado'])

    def test_empty_download_is_still_error(self):
        self.assertEqual(self.evaluate([])['estado_validacion'], 'DATA_ERROR')

    def test_invalid_overlap_is_error(self):
        for fields in [dict(high=float('nan')), dict(low=200),
                       dict(fin='broken'), dict(fin='2026-09-01T00:00:00Z')]:
            with self.subTest(fields=fields):
                self.assertEqual(self.evaluate([{**BAR, **fields}])['estado_validacion'], 'DATA_ERROR')

    def test_invalid_mixed_with_overlap_is_error(self):
        self.assertEqual(self.evaluate([BAR, {}])['estado_validacion'], 'DATA_ERROR')

    def test_duplicate_excluded_bars_are_error(self):
        self.assertEqual(self.evaluate([BAR, BAR])['estado_validacion'], 'DATA_ERROR')

    def test_expired_no_usable_data_stays_error(self):
        result = self.evaluate([BAR], fecha_limite='2026-09-10')
        self.assertEqual(result['estado_validacion'], 'DATA_ERROR')

    def test_expired_overlap_stays_error(self):
        later = datetime(2026, 9, 23, 14, tzinfo=timezone.utc)
        result = v.evaluar(PREDICTION, lambda *args: [BAR], later)
        self.assertEqual(result['estado_validacion'], 'DATA_ERROR')

    def test_real_provider_failure_stays_error(self):
        def fail(*args):
            raise TimeoutError()
        self.assertEqual(v.evaluar(PREDICTION, fail, NOW)['estado_validacion'], 'DATA_ERROR')

    def test_valid_eligible_touch_unchanged(self):
        result = self.evaluate([{**BAR, 'inicio': '2026-09-11T00:00:00Z'}])
        self.assertEqual((result['estado_validacion'], result['resultado']), ('RESOLVED', 1))

    def test_six_observed_cases_pending_without_result(self):
        fixture = Path(__file__).parent / 'fixtures' / 'validation_temporal_overlap.json'
        for case in json.loads(fixture.read_text())['cases']:
            with self.subTest(prediction_id=case['id']):
                result = self.evaluate(case['bars'], creado=case['created'],
                                       instrumento=case['instrument'])
                self.assertEqual((result['estado_validacion'], result['resultado']), ('PENDING', None))


class TemporalPersistenceTests(unittest.TestCase):
    def test_error_to_pending_preserves_contract_audits_and_final_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'validation.db')
            with patch.object(db, 'DB_PATH', path), patch.object(memoria, 'DB_PATH', path):
                memoria.init_memoria()
                with patch.object(memoria, '_ahora', return_value=PREDICTION['creado']):
                    pid = memoria.registrar_prediccion(
                        afirmacion='WTI tocará 100', probabilidad=.35, horizonte='2026-09-22',
                        instrumento='WTI', nivel_objetivo=100, condicion='>=', modo='toca',
                        fecha_limite='2026-09-22', provider='fixture', model='fixture',
                        model_snapshot='fixture', prompt_version='fixture',
                    )['prediccion_id']
                with sqlite3.connect(path) as c:
                    c.row_factory = sqlite3.Row
                    original = dict(c.execute('SELECT * FROM predicciones WHERE id=?', (pid,)).fetchone())
                old = v.evaluar(original, lambda *args: [], NOW)
                self.assertTrue(v.guardar_evaluacion(pid, old)['ok'])
                new = v.evaluar(original, lambda *args: [BAR], NOW)
                self.assertTrue(v.guardar_evaluacion(pid, new)['ok'])
                self.assertTrue(v.guardar_evaluacion(pid, new)['sin_cambios'])
                with sqlite3.connect(path) as c:
                    c.row_factory = sqlite3.Row
                    current = dict(c.execute('SELECT * FROM predicciones WHERE id=?', (pid,)).fetchone())
                    from config import CAMPOS_INMUTABLES
                    self.assertTrue(all(original[k] == current[k] for k in CAMPOS_INMUTABLES))
                    self.assertEqual(current['estado_validacion'], 'PENDING')
                    self.assertEqual((current['resultado'], current['resuelto'], current['brier']), (None, None, None))
                    audits = c.execute('SELECT evidencia FROM validacion_auditoria ORDER BY id').fetchall()
                    self.assertEqual([json.loads(a[0])['estado_validacion'] for a in audits], ['DATA_ERROR', 'PENDING'])
                final = v.evaluar(original, lambda *args: [{**BAR, 'inicio': '2026-09-11T00:00:00Z'}], NOW)
                self.assertTrue(v.guardar_evaluacion(pid, final)['ok'])
                self.assertFalse(v.guardar_evaluacion(pid, new)['ok'])
                with sqlite3.connect(path) as c:
                    self.assertEqual(c.execute('SELECT estado_validacion,resultado FROM predicciones WHERE id=?', (pid,)).fetchone(), ('RESOLVED', 1))


if __name__ == '__main__':
    unittest.main()
