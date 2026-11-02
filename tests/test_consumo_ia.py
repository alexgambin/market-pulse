import ast
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import ai_pricing
import consumo_ia as obs
import db


def fingerprint(value):
    # Normalización portable entre AST de Python 3.12 y 3.13 (campos vacíos).
    def normalized(node):
        if isinstance(node, ast.AST):
            return [type(node).__name__, {k: normalized(v) for k,v in ast.iter_fields(node)
                                         if v is not None and v != []}]
        if isinstance(node, list):
            return [normalized(v) for v in node]
        return node
    return hashlib.sha256(json.dumps(normalized(value), sort_keys=True).encode()).hexdigest()


def response(model='claude-sonnet-4-6', usage=True):
    return SimpleNamespace(model=model, usage=dict(input_tokens=1000, output_tokens=200,
        cache_read_input_tokens=3000, cache_creation_input_tokens=500,
        cache_creation={'ephemeral_5m_input_tokens': 400, 'ephemeral_1h_input_tokens': 100}) if usage else None)


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        dbpatch = patch.object(db, 'DB_PATH', str(Path(self.tmp.name) / 'test.db'))
        dbpatch.start(); self.addCleanup(dbpatch.stop)
        self.assertTrue(obs.init_consumo())

    def rows(self):
        with db._conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM consumo_ia ORDER BY id')]

    def call(self, result=None, contexto=None, **kwargs):
        r = result or response()
        create = AsyncMock(return_value=r)
        out = asyncio.run(obs.llamar(create, contexto, model=r.model, **kwargs))
        self.assertIs(out, r)
        return self.rows()[-1]

class CostTests(DatabaseCase):
    def test_usage_official_counters_and_known_prices(self):
        r = self.call()
        self.assertEqual((r['input_tokens'], r['output_tokens'], r['cache_read_tokens'], r['cache_creation_tokens'], r['total_tokens']),
                         (1000,200,3000,500,4700))
        self.assertAlmostEqual(r['coste_input'], .003)
        self.assertAlmostEqual(r['coste_output'], .003)
        self.assertAlmostEqual(r['coste_cache'], .0009+.0015+.0006)
        self.assertAlmostEqual(r['coste_total'], .009)
        self.assertEqual(r['pricing_version'], ai_pricing.PRICING_VERSION)
        self.assertEqual(r['success'], 1)

    def test_unknown_model_preserves_tokens(self):
        r = self.call(response(model='modelo-desconocido'))
        self.assertEqual(r['total_tokens'], 4700)
        self.assertIsNone(r['coste_total'])
        self.assertIsNone(r['pricing_version'])

    def test_absent_cache_consistent_zero_and_missing_usage_null(self):
        r = response(); r.usage = {'input_tokens': 10, 'output_tokens': 2}
        saved = self.call(r)
        self.assertEqual((saved['cache_read_tokens'],saved['cache_creation_tokens'],saved['total_tokens']), (0,0,12))
        saved = self.call(response(usage=False))
        self.assertIsNone(saved['input_tokens']); self.assertIsNone(saved['coste_total'])

    def test_unknown_cache_ttl_keeps_tokens_without_inventing_cost(self):
        r = response(); del r.usage['cache_creation']
        saved = self.call(r)
        self.assertEqual(saved['cache_creation_tokens'], 500)
        self.assertIsNone(saved['coste_total'])

    def test_contexts_and_schedule_hour_preserved(self):
        self.call(contexto=obs.Contexto('pasada_programada','18:00'))
        self.call(contexto=obs.Contexto('pasada_vigilante','ruptura'))
        self.call(contexto=obs.Contexto('pasada_programada','18:00'), componente='triaje')
        rows = self.rows()
        self.assertEqual([(r['componente'],r['subcomponente']) for r in rows],
                         [('pasada_programada','18:00'),('pasada_vigilante','ruptura'),('triaje','18:00')])
        self.assertEqual(rows[2]['origen'], 'pasada_programada')

    def test_failure_recorded_original_exception_preserved(self):
        error = RuntimeError('SECRET prompt and api key')
        create = AsyncMock(side_effect=error)
        with self.assertRaises(RuntimeError) as caught:
            asyncio.run(obs.llamar(create, obs.Contexto('evento_reaccion'), model='claude-sonnet-4-6'))
        self.assertIs(caught.exception, error)
        row = self.rows()[0]
        self.assertEqual(row['success'], 0)
        self.assertEqual(row['error'], 'RuntimeError')
        self.assertIsNone(row['total_tokens']); self.assertIsNone(row['coste_total'])
        self.assertNotIn('SECRET', json.dumps(row))

    def test_error_usage_is_preserved_if_provided(self):
        error = RuntimeError('ignored'); error.body = {'usage':response().usage}; error.status_code = 429
        with self.assertRaises(RuntimeError):
            asyncio.run(obs.llamar(AsyncMock(side_effect=error), model='claude-sonnet-4-6'))
        self.assertEqual(self.rows()[0]['total_tokens'], 4700)
        self.assertEqual(self.rows()[0]['error'], 'RuntimeError HTTP 429')

    def test_insert_failure_does_not_break_success_or_expose_secret(self):
        r = response()
        ctx = obs.Contexto()
        with patch.object(db, '_conn', side_effect=sqlite3.OperationalError('SECRET')), self.assertLogs(obs.log, 'WARNING') as logs:
            self.assertIs(asyncio.run(obs.llamar(AsyncMock(return_value=r),ctx,model=r.model)), r)
        self.assertEqual(ctx.registros_perdidos, 1)
        self.assertNotIn('SECRET', ' '.join(logs.output))

    def test_recorder_itself_failing_does_not_break_success(self):
        r = response()
        with patch.object(obs, 'registrar', side_effect=RuntimeError('SECRET')), self.assertLogs(obs.log, 'WARNING'):
            self.assertIs(asyncio.run(obs.llamar(AsyncMock(return_value=r), model=r.model)),r)

    def test_arguments_and_response_untouched(self):
        create = AsyncMock(return_value=response())
        kwargs = dict(model='claude-sonnet-4-6', max_tokens=4000, system=[{'text':'unchanged'}],
                      messages=[{'role':'user','content':'unchanged'}], tools=[{'name':'tool'}])
        asyncio.run(obs.llamar(create,obs.Contexto('pasada_manual'),**kwargs))
        create.assert_awaited_once_with(**kwargs)

    def test_productivity_and_total_include_triage(self):
        ctx = obs.iniciar_ejecucion('pasada_programada','06:45')
        obs.contar_noticias(ctx,noticias_consideradas=40,noticias_candidatas=20,noticias_relevantes=8)
        obs.contar_herramienta(ctx,'crear_hilo',{}, {'ok':True,'hilo_id':1})
        for _ in range(2): obs.contar_herramienta(ctx,'actualizar_hilo',{'hilo_id':1}, {'ok':True})
        obs.contar_herramienta(ctx,'registrar_prediccion',{}, {'ok':True,'prediccion_id':1})
        obs.contar_herramienta(ctx,'crear_hilo',{}, {'error':'exists'})
        self.call(contexto=ctx,componente='triaje'); self.call(contexto=ctx)
        obs.finalizar_ejecucion(ctx,True)
        with db._conn() as c: row=dict(c.execute('SELECT * FROM ejecuciones_agente').fetchone())
        self.assertEqual((row['noticias_consideradas'],row['noticias_relevantes'],row['hilos_creados'],row['hilos_actualizados'],row['predicciones_creadas']), (40,8,1,1,1))
        self.assertAlmostEqual(row['coste_ia_total'],.018)

    def test_unknown_or_lost_cost_does_not_become_zero(self):
        ctx = obs.iniciar_ejecucion('pasada_manual')
        self.call(contexto=ctx,result=response('unknown'))
        obs.finalizar_ejecucion(ctx,True)
        with db._conn() as c:
            self.assertIsNone(c.execute('SELECT coste_ia_total FROM ejecuciones_agente').fetchone()[0])
        data=obs.resumen(ahora=datetime.now(timezone.utc)+timedelta(seconds=1))
        self.assertIsNone(data['total']['coste_total'])
        self.assertIsNone(data['proyeccion_mensual_30d'])

    def test_migration_idempotent_preserves_existing_tables(self):
        with db._conn() as c:
            c.execute('CREATE TABLE existente (v TEXT)'); c.execute("INSERT INTO existente VALUES ('intacto')")
        self.call(); self.assertTrue(obs.init_consumo()); self.assertTrue(obs.init_consumo())
        self.assertEqual(len(self.rows()), 1)
        with db._conn() as c:
            self.assertEqual(c.execute('SELECT v FROM existente').fetchone()[0], 'intacto')

    def test_windows_and_grouping(self):
        for timestamp, component, sub in [('2026-09-07T10:00:00+00:00','pasada_programada','10:00'),
                                          ('2026-09-08T06:00:00+00:00','pasada_vigilante','ruptura')]:
            obs.registrar(obs.Contexto(component,sub),None,'claude-sonnet-4-6',respuesta=response(),timestamp=timestamp)
        now=datetime(2026,9,8,12,tzinfo=timezone.utc)
        self.assertEqual(obs.resumen(24,ahora=now)['total']['llamadas'],1)
        data=obs.resumen(48,ahora=now)
        self.assertEqual(data['total']['llamadas'],2)
        self.assertEqual(len(data['grupos']),2)
        self.assertEqual([g['porcentaje_del_coste'] for g in data['grupos']],[50,50])
        self.assertEqual(obs.resumen(dia='2026-09-07',ahora=now)['total']['llamadas'],1)

    def test_concurrent_components_do_not_leak_context(self):
        async def run():
            await asyncio.gather(
                obs.llamar(AsyncMock(return_value=response()),obs.Contexto('pasada_programada','14:00'),model='claude-sonnet-4-6'),
                obs.llamar(AsyncMock(return_value=response()),obs.Contexto('evento_reaccion','IPC'),model='claude-sonnet-4-6'))
        asyncio.run(run())
        self.assertEqual({(r['componente'],r['subcomponente']) for r in self.rows()},
                         {('pasada_programada','14:00'),('evento_reaccion','IPC')})

    def test_local_day_handles_dst(self):
        data=obs.resumen(dia='2026-10-25')
        hours=(datetime.fromisoformat(data['fin'])-datetime.fromisoformat(data['inicio'])).total_seconds()/3600
        self.assertEqual(hours,25)

    def test_frozen_analytics_and_provider_arguments(self):
        baseline=json.loads(Path('tests/observability_contracts.json').read_text())
        for file, expected in baseline.items():
            tree=ast.parse(Path(file).read_text()); constants={}; calls=[]
            for n in tree.body:
                if isinstance(n,ast.Assign):
                    for target in n.targets:
                        if isinstance(target,ast.Name) and target.id.isupper():
                            constants[target.id]=fingerprint(n.value)
            for n in ast.walk(tree):
                if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='llamar':
                    original=[k for k in n.keywords if k.arg!='componente']
                    calls.append(fingerprint(original))
            self.assertEqual(constants,expected['constants'],file)
            self.assertEqual(sorted(calls),expected['provider_calls'],file)


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ('fastapi','anthropic','httpx','apscheduler','yfinance')), 'Requiere entorno mp')
class ApplicationTests(DatabaseCase):
    # setUp DB de observabilidad compartida por los tests de integración offline.
    def setUp(self):
        super().setUp()
        import main, informes, agente, memoria
        self.main,self.informes,self.agente=main,informes,agente
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(main.estado, {'ejecutando':False,'ultima_ruptura':None,'base_vigilante':None}))
        self.stack.enter_context(patch.dict('os.environ', {'ANTHROPIC_API_KEY':'mock-key','TELEGRAM_BOT_TOKEN':'SECRET-TELEGRAM'}))
        self.stack.enter_context(patch.object(memoria,'DB_PATH',db.DB_PATH))
        db.init_db(); memoria.init_memoria()

    def test_actual_sdk_usage_object(self):
        from anthropic.types import Message
        r=Message(id='msg_test',type='message',role='assistant',model='claude-sonnet-4-6',
                  content=[{'type':'text','text':'intacto'}], stop_reason='end_turn', usage=response().usage)
        saved=self.call(r)
        self.assertEqual(saved['total_tokens'],4700)
        self.assertAlmostEqual(saved['coste_total'],.009)

    def test_api_returns_24h_48h_day_and_rejects_invalid_window(self):
        from fastapi.testclient import TestClient
        self.call()
        with patch.object(self.main,'scheduler',MagicMock()), TestClient(self.main.app) as client:
            for query in ['horas=24','horas=48','dia=2026-09-08']:
                r=client.get('/api/consumo-ia?'+query)
                self.assertEqual(r.status_code,200)
                self.assertIn('grupos',r.json())
            self.assertEqual(client.get('/api/consumo-ia?horas=72').status_code,422)
            self.assertEqual(client.get('/api/consumo-ia?dia=invalid').status_code,422)

    def test_event_error_retains_existing_output_and_records_usage(self):
        cliente=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError('SECRET'))))
        event=dict(titulo='IPC', divisa='USD',impacto='alto',momento=datetime.now(timezone.utc))
        with patch.object(self.agente.anthropic,'AsyncAnthropic',return_value=cliente), patch.object(self.agente.precios,'cinta',AsyncMock(return_value='')), patch.object(self.agente.cot,'resumen',AsyncMock(return_value='')):
            result=asyncio.run(self.agente.analizar_evento('mock',event,'reaccion'))
        self.assertEqual(result,'[error analizando evento: RuntimeError]')
        self.assertEqual(self.rows()[0]['componente'],'evento_reaccion')
        self.assertEqual(self.rows()[0]['success'],0)

    def test_triage_selection_unchanged(self):
        r=response('claude-haiku-4-5-20251001')
        r.content=[SimpleNamespace(type='text',text='[{"id":1,"score":9},{"id":2,"score":1}]')]
        cliente=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=r)))
        news=[{'id':1,'titular':'a'},{'id':2,'titular':'b'}]
        output=asyncio.run(self.agente._triaje(cliente,news,contexto=obs.Contexto('pasada_programada','21:45')))
        self.assertEqual(output,[news[0]])
        self.assertEqual(self.rows()[0]['componente'],'triaje')
        self.assertEqual(self.rows()[0]['subcomponente'],'21:45')

    def test_telegram_disabled_no_http_no_secrets(self):
        import config
        with patch.object(config,'TELEGRAM_ENABLED',False), patch.object(self.informes.httpx,'AsyncClient') as web, self.assertLogs(obs.log,'INFO') as logs:
            asyncio.run(self.informes.enviar_telegram('contenido privado'))
        web.assert_not_called()
        self.assertNotIn('SECRET', ' '.join(logs.output))
        self.assertNotIn('contenido privado', ' '.join(logs.output))

    def test_disabled_telegram_still_generates_measures_and_archives_report(self):
        import config
        r=response(); r.content=[SimpleNamespace(type='text',text='Informe intacto')]
        client=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=r)))
        with ExitStack() as stack:
            stack.enter_context(patch.object(config,'TELEGRAM_ENABLED',False))
            stack.enter_context(patch.object(self.informes.anthropic,'AsyncAnthropic',return_value=client))
            for module, method in [(self.informes.precios,'cinta'),(self.informes.cot,'resumen'),(self.informes.calendario,'proximos'),(self.informes.fred,'macro'),(self.informes.eia,'energia')]:
                stack.enter_context(patch.object(module,method,AsyncMock(return_value='')))
            stack.enter_context(patch.object(self.informes,'_contexto',return_value='contexto'))
            stack.enter_context(patch.object(self.informes,'cargar_doctrina',return_value=''))
            web=stack.enter_context(patch.object(self.informes.httpx,'AsyncClient'))
            asyncio.run(self.main.informe('overnight'))
        web.assert_not_called(); client.messages.create.assert_awaited_once()
        self.assertEqual(db.ultimos_analisis(1)[0]['texto'],'[Informe overnight]\n\nInforme intacto')
        self.assertEqual(self.rows()[0]['componente'],'informe_manana')
        self.assertEqual(self.rows()[0]['total_tokens'],4700)

    def test_scheduled_jobs_keep_times_and_explicit_context(self):
        scheduler=MagicMock()
        async def start():
            with patch.object(self.main,'scheduler',scheduler):
                async with self.main.lifespan(self.main.app): pass
        asyncio.run(start())
        calls=[c for c in scheduler.add_job.call_args_list if c.kwargs.get('id','').startswith('pasada_')]
        self.assertEqual([c.kwargs['kwargs']['subcomponente'] for c in calls],['06:45','10:00','14:00','18:00','21:45'])
        for call in calls:
            self.assertEqual(call.kwargs['kwargs']['componente'],'pasada_programada')
        watch=next(c for c in scheduler.add_job.call_args_list if c.kwargs.get('id')=='vigilante')
        self.assertIn("minute='*/15'", str(watch.args[1]))

    def test_watcher_propagates_trigger_without_programmed_label(self):
        self.main.estado['base_vigilante']={'WTI':{'ultimo':100}}
        target=AsyncMock()
        async def run():
            with patch.object(self.main.precios,'instantanea',AsyncMock(return_value={'WTI':{'ultimo':103}})), patch.object(self.main,'pasada',target):
                await self.main.vigilante(); await asyncio.sleep(0)
        asyncio.run(run())
        self.assertEqual(target.call_args.kwargs['componente'],'pasada_vigilante')
        trigger=target.call_args.kwargs['metadatos']['disparos'][0]
        self.assertEqual(trigger['instrumento'],'WTI'); self.assertAlmostEqual(trigger['variacion_pct'],3)

    def test_programmed_execution_context_reaches_agent_and_finalizes(self):
        async def agent(key,news,contexto=None):
            self.assertEqual((contexto.componente,contexto.subcomponente),('pasada_programada','18:00'))
            obs.contar_noticias(contexto,noticias_relevantes=1)
            await obs.llamar(AsyncMock(return_value=response()),contexto,model='claude-sonnet-4-6')
            return 'sin cambios'
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.main.fuentes,'recolectar',return_value=([],{})))
            stack.enter_context(patch.object(db,'noticias_pendientes',return_value=[{'id':1}]))
            stack.enter_context(patch.object(self.main,'ejecutar_agente',agent))
            stack.enter_context(patch.object(self.main,'_evaluar_pasada_extra'))
            stack.enter_context(patch.object(self.main,'_detectar_convergencia'))
            asyncio.run(self.main.pasada('pasada_programada','18:00'))
        with db._conn() as c: row=dict(c.execute('SELECT * FROM ejecuciones_agente').fetchone())
        self.assertEqual(row['noticias_consideradas'],1)
        self.assertEqual(row['noticias_relevantes'],1)
        self.assertIsNotNone(row['fin']); self.assertEqual(row['success'],1)
        self.assertAlmostEqual(row['coste_ia_total'],.009)
