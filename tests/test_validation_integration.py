"""Integración offline; ejecutar con el entorno conda mp del proyecto."""
import asyncio
from datetime import date
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import db
import memoria
import precios
import validation

HAS_APP = all(importlib.util.find_spec(m) for m in ('fastapi', 'anthropic', 'httpx', 'apscheduler', 'yfinance'))


@unittest.skipUnless(HAS_APP, 'Requiere las dependencias del entorno mp')
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        import main
        from fastapi.testclient import TestClient
        self.main = main
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for module in (db, memoria):
            mock = patch.object(module, 'DB_PATH', str(Path(self.temp.name) / 'test.db'))
            mock.start(); self.addCleanup(mock.stop)
        db.init_db(); memoria.init_memoria()
        self.client = TestClient(main.app)  # Sin lifespan: no arranca otros jobs.
        self.addCleanup(self.client.close)

    def register(self):
        import agente
        args = dict(afirmacion='Mantiene el nivel', probabilidad=.2, horizonte='2026-09-01',
                    tipo='evento', instrumento='Oro', nivel_objetivo=100,
                    condicion='>=', modo='mantiene', fecha_limite='2026-09-01')
        response = asyncio.run(agente._ejecutar_herramienta('registrar_prediccion', args, model_snapshot='respuesta-modelo'))
        return response['prediccion_id']

    def test_agent_registers_original_provenance_and_cannot_resolve(self):
        import agente
        ident = self.register()
        self.assertNotIn('resolver_prediccion', [t['name'] for t in agente.HERRAMIENTAS])
        result = asyncio.run(agente._ejecutar_herramienta('resolver_prediccion', {'prediccion_id': ident, 'resultado': 1}))
        self.assertIn('error', result)
        with db._conn() as c:
            p = dict(c.execute('SELECT * FROM predicciones').fetchone())
        self.assertEqual((p['schema_version'], p['model_snapshot'], p['modo']), (2,'respuesta-modelo','mantiene'))
        self.assertEqual(p['estado_validacion'], 'PENDING')

    def test_manual_api_explicit_outcome_and_audit(self):
        ident = self.register()
        validation.ejecutar_validacion()
        url = f'/api/predicciones/{ident}/resolver'
        self.assertEqual(self.client.post(url, json={}).status_code, 422)
        self.assertEqual(self.client.post(url, json={'resultado': -1}).status_code, 422)
        r = self.client.post(url, json={'resultado': 0, 'fuente_resolucion': 'Fuente verificable',
                                        'fecha_resolucion': date.today().isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(r.json()['brier'], .04)
        self.assertEqual(self.client.post(url, json={'resultado': 0, 'fuente_resolucion': 'Otra fuente',
                                        'fecha_resolucion': date.today().isoformat()}).status_code, 409)
        audit = self.client.get(f'/api/predicciones/{ident}/auditoria').json()
        self.assertEqual(len(audit), 2)
        self.assertEqual(self.client.get('/api/calibracion?schema_version=1').json()['n'], 0)
        self.assertEqual(self.client.get('/api/calibracion?schema_version=2&resuelto_por=manual&specification_source=original').json()['n'], 1)

    def test_review_endpoint_separates_technical_failures(self):
        self.register(); validation.ejecutar_validacion()
        r = self.client.get('/api/predicciones/revision')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['NEEDS_REVIEW']), 1)
        self.assertEqual(r.json()['DATA_ERROR'], [])

    def test_job_and_scheduler_wiring(self):
        self.register()
        with patch.object(self.main, 'scheduler', MagicMock()) as scheduler:
            async def check():
                async with self.main.lifespan(self.main.app):
                    await self.main.validar_predicciones()
            asyncio.run(check())
            job = next(c for c in scheduler.add_job.call_args_list if c.kwargs.get('id') == 'validacion_predicciones')
            self.assertEqual(job.kwargs['max_instances'], 1)
            self.assertTrue(job.kwargs['coalesce'])
        self.assertEqual(self.main.estado['validacion']['estados'], {'NEEDS_REVIEW': 1})

    def test_yfinance_adapter_raw_ohlc_timezone_and_range(self):
        import pandas as pd
        frame = pd.DataFrame({'High': [101.], 'Low': [90.], 'Close': [95.]},
                             index=pd.DatetimeIndex(['2026-09-02'], tz='America/New_York'))
        with patch('yfinance.Ticker') as ticker:
            ticker.return_value.history.return_value = frame
            bars = precios.historial_validacion('Oro', date(2026,9,2), date(2026,9,5))
            kwargs = ticker.return_value.history.call_args.kwargs
            self.assertFalse(kwargs['auto_adjust']); self.assertFalse(kwargs['back_adjust'])
            self.assertTrue(kwargs['raise_errors']); self.assertEqual(kwargs['end'], '2026-09-06')
            self.assertEqual(bars[0]['inicio'], '2026-09-01T00:00:00-04:00')
            self.assertEqual(bars[0]['fin'], '2026-09-03T00:00:00-04:00')

    def test_yfinance_without_timezone_fails(self):
        import pandas as pd
        frame = pd.DataFrame({'High': [101.], 'Low': [90.], 'Close': [95.]},
                             index=pd.DatetimeIndex(['2026-09-02']))
        with patch('yfinance.Ticker') as ticker:
            ticker.return_value.history.return_value = frame
            with self.assertRaises(ValueError):
                precios.historial_validacion('Oro', date(2026,9,2), date(2026,9,5))
