"""Vista de las fuentes COT actuales. Fixtures offline, sin activar POSITIONING IA."""
import asyncio
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

import cot
from positioning import presentation as view


NOW = datetime(2026,9,9,12,tzinfo=timezone.utc)
POINTS = [{"fecha":"2026-08-25","neto":-120}, {"fecha":"2026-09-01","neto":0}]


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.object(view, "_last_good", {})
        self.cache.start(); self.addCleanup(self.cache.stop)

    def test_families_and_participants_remain_distinct(self):
        gold = view.describe("Oro", POINTS, now=NOW)
        yen = view.describe("Yen (6J vs USD)", POINTS, now=NOW)
        self.assertEqual((gold['report_type'],gold['participant_category']), ('DISAGGREGATED','MANAGED_MONEY'))
        self.assertEqual((yen['report_type'],yen['participant_category']), ('TFF','LEVERAGED_FUNDS'))
        self.assertEqual(yen['data_kind'],'OBSERVED_CFTC')
        self.assertEqual(yen['instrument'],'USD/JPY')
        self.assertIn('no se invierte',yen['note'])
        self.assertEqual(yen['market_code'],'097741')

    def test_zero_is_data_weekly_change_and_age_are_explicit(self):
        item = view.describe('WTI',POINTS,now=NOW)
        self.assertEqual((item['net'],item['weekly_change'],item['age_days']), (0,120,8))
        self.assertFalse(item['age_warning'])
        self.assertIsNone(item['published_at']); self.assertIsNone(item['fetched_at'])

    def test_gaps_and_single_report_do_not_create_weekly_change(self):
        for points in ([POINTS[0]], [POINTS[0],{'fecha':'2026-09-08','neto':10}]):
            self.assertIsNone(view.describe('WTI',points,now=NOW)['weekly_change'])

    def test_sort_dedup_and_conflicting_duplicate(self):
        good = view.describe('WTI',[POINTS[1],POINTS[0],POINTS[0]],now=NOW)
        self.assertEqual(len(good['history']),2)
        bad = view.describe('WTI',[*POINTS,{'fecha':'2026-09-01','neto':1}],now=NOW)
        self.assertEqual(bad['error'],'CONFLICTING_DUPLICATES')
        self.assertIsNone(bad['net'])

    def test_invalid_and_empty_data_are_not_neutral(self):
        cases = [[],[{'fecha':'2026-09-01','neto':None}],[{'fecha':'2026-09-01','neto':float('nan')}],
                 [{'fecha':'2026-09-01','neto':float('inf')}],[{'fecha':'2026-09-01','neto':True}],
                 [{'fecha':'2026-09-01','neto':.5}],[{'fecha':'2026-09-10','neto':1}],
                 [{'fecha':'invalid','neto':1}],[{'fecha':'2026-02-30','neto':1}]]
        for points in cases:
            with self.subTest(points=points):
                item=view.describe('WTI',points,now=NOW)
                self.assertEqual(item['status'],'UNAVAILABLE')
                self.assertIsNone(item['net']); self.assertEqual(item['history'],[])

    def test_old_report_has_explicit_age_warning(self):
        item=view.describe('WTI',[{'fecha':'2026-08-18','neto':12}],now=NOW)
        self.assertEqual(item['age_days'],22); self.assertTrue(item['age_warning'])

    def test_error_retains_last_good_without_changing_cot_summary_cache(self):
        before=dict(cot._cache)
        async def run():
            with patch.object(cot,'historico',AsyncMock(return_value=POINTS)):
                first=await view.overview()
            with patch.object(cot,'historico',AsyncMock(side_effect=RuntimeError('provider'))):
                second=await view.overview()
            return first,second
        first,second=asyncio.run(run())
        self.assertEqual(len(first['items']),6)
        for old,new in zip(first['items'],second['items']):
            self.assertEqual(new['status'],'LAST_VALID')
            self.assertEqual(new['report_date'],old['report_date'])
            self.assertEqual(new['history'],old['history'])
        self.assertEqual(cot._cache,before)

    def test_report_regression_does_not_replace_newer_data(self):
        async def run():
            with patch.object(cot,'historico',AsyncMock(return_value=POINTS)):
                await view.overview()
            with patch.object(cot,'historico',AsyncMock(return_value=[POINTS[0]])):
                return await view.overview()
        data=asyncio.run(run())
        self.assertTrue(all(i['status']=='LAST_VALID' and i['error']=='REPORT_REGRESSION' for i in data['items']))

    def test_api_uses_existing_six_sources_and_serves_native_frontend(self):
        import main
        from fastapi.testclient import TestClient
        # Sin lifespan: no arranca scheduler ni usa la base real.
        client=TestClient(main.app)
        self.addCleanup(client.close)
        with patch.object(cot,'historico',AsyncMock(return_value=POINTS)) as history:
            response=client.get('/api/cot/overview')
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data['api_version'],'cot-overview-v1')
        self.assertEqual({i['key'] for i in data['items']},set(cot.CONTRATOS))
        self.assertEqual(history.await_count,6)
        self.assertNotIn('positioning_score',data['items'][0])
        html=client.get('/').text
        self.assertIn('data-vista="positioning"',html)
        self.assertIn('/static/positioning.js',html)
        self.assertEqual(client.get('/static/positioning.js').status_code,200)


if __name__=='__main__':
    unittest.main()
