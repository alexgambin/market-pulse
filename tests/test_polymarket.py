"""Reset y ámbito de Polymarket: SQLite temporal, HTTP y puente simulados."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import polymarket as pm
from polymarket_policy import financial_category, identity_keys
import puente


def market(**changes):
    data = dict(id="101", question="Will Bitcoin be above $70,000 on September 30?",
                outcomePrices='["0.90", "0.10"]', outcomes='["Yes", "No"]',
                volume24hr=20000, active=True, closed=False,
                endDate=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                events=[{"id": "event-1", "title": "Bitcoin price"}], conditionId="0xabc")
    data.update(changes)
    return data


def response(data, status=200):
    return Mock(status_code=status, json=Mock(return_value=data))


class FinancialPolicyTests(unittest.TestCase):
    def test_financial_questions(self):
        for question, category in [
            ("Will Bitcoin be above $70,000?", "Crypto"),
            ("Will Ethereum reach $4,000?", "Crypto"),
            ("Will Gold close above $3,000?", "Finance"),
            ("Will WTI reach $100?", "Finance"),
            ("Will EUR/USD be above 1.20?", "Finance"),
            ("Will the S&P 500 close above 6500?", "Finance"),
            ("Will United States inflation be above 3%?", "Finance"),
            ("Will the Fed cut rates in September?", "Finance"),
        ]:
            with self.subTest(question=question):
                self.assertEqual(financial_category(market(question=question)), category)

    def test_exclusions_override_financial_keywords_and_tags(self):
        for question in [
            "Will Elon Musk buy Bitcoin?", "Will Musk tweet Bitcoin above $70,000?",
            "Will Elon make 200-219 posts?", "Will NBA revenue be above $10 billion?",
            "Will Real Madrid win on 2026-09-30?", "Will a football club buy Bitcoin?",
            "Will Israel close its airspace by September 30?",
            "Strait of Hormuz traffic returns to normal by September 30?",
            "Will Trump win the election?", "Will it rain tomorrow?",
            "Will a movie reach $100 million at the box office?",
            "Will Powell mention Bitcoin price above $70,000?",
            "Will a celebrity reach 10 million followers?",
        ]:
            with self.subTest(question=question):
                candidate = market(question=question, tags=[{"label": "Crypto"}],
                                   description="Bitcoin prices and the stock market")
                self.assertIsNone(financial_category(candidate))
                self.assertIsNone(pm.evaluate_market(candidate))

    def test_sports_and_musk_event_metadata_block_price_question(self):
        for changes in [{"tags": [{"label": "Sports"}]},
                        {"events": [{"id": "x", "title": "Elon Musk predictions"}]}]:
            self.assertIsNone(pm.evaluate_market(market(**changes)))

    def test_unknown_and_description_only_are_rejected(self):
        self.assertIsNone(financial_category(market(question="Will the bridge open?",
                                                   description="Bitcoin above 70000", tags=["Finance"])))

    def test_finance_retains_existing_strategy_threshold(self):
        self.assertIsNone(pm.evaluate_market(market(question="Will Gold reach $4,000?")))
        self.assertLess(pm.BECKER_GAPS["Finance"], pm.CONFIG["min_gap"])

    def test_invalid_values_and_expired_markets(self):
        for changes in [{"id": None}, {"question": ""}, {"outcomePrices": '["NaN", "0.1"]'},
                        {"outcomePrices": '["bad", "0.1"]'}, {"outcomePrices": '[1.2, -0.2]'},
                        {"outcomePrices": '{"a": 1}'}, {"volume24hr": "nan"},
                        {"outcomes": '["Up", "Down"]'}, {"active": False}, {"closed": True},
                        {"endDate": None}, {"endDate": "not a date"},
                        {"endDate": "2020-01-01T00:00:00Z"}]:
            with self.subTest(changes=changes):
                self.assertIsNone(pm.evaluate_market(market(**changes)))

    def test_yes_no_order_is_explicit(self):
        signal = pm.evaluate_market(market(outcomes='["No", "Yes"]', outcomePrices='[0.1,0.9]'))
        self.assertEqual(signal["yes_price"], 90)

    def test_identity_normalization_and_provider_events(self):
        keys = identity_keys(market(id=101, question="  WILL Bitcoin be above $70,000?  ",
                                    events=[{"id": 4}, {"id": "5"}]))
        self.assertIn(("market", "101"), keys)
        self.assertIn(("question", "will bitcoin be above $70,000"), keys)
        self.assertIn(("event", "4"), keys)
        self.assertIn(("event", "5"), keys)


class PaperDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "paper.db")
        for module in (pm, puente):
            patcher = patch.object(module, "DB_PATH", self.path)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(puente, "evaluar", AsyncMock(return_value=None))
        self.bridge = patcher.start()
        self.addCleanup(patcher.stop)
        pm.init_db()
        self.generation = pm._get_estado("generation")

    def insert(self, **changes):
        return pm._insert_position(pm.evaluate_market(market(**changes)), self.generation)

    def reset(self):
        return pm.reset_paper(Path(self.temp.name) / "before-reset.db")

    def count(self, table="pm_trades"):
        with sqlite3.connect(self.path) as c:
            return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_migration_is_idempotent_and_keeps_balances(self):
        self.insert()
        pm._set_estado("bankroll", 9999)
        pm.init_db()
        pm.init_db()
        self.assertEqual(self.count(), 1)
        self.assertEqual(pm._bankroll(), 9999)
        self.assertEqual(pm._get_estado("generation"), self.generation)
        self.assertIsNone(self.insert())

    def test_no_repeat_by_market_condition_question_or_event(self):
        self.assertIsNotNone(self.insert())
        for changes in [
            dict(id=101, conditionId="new", events=[], question="Will Bitcoin reach $80,000?"),
            dict(id="102", conditionId="0xabc", events=[], question="Will Bitcoin reach $80,000?"),
            dict(id="102", conditionId="new", events=[], question=" WILL Bitcoin be above $70,000 on September 30? "),
            dict(id="102", conditionId="new", question="Will Bitcoin reach $80,000?"),
        ]:
            self.assertIsNone(self.insert(**changes))
        self.assertEqual(self.count(), 1)

    def test_closed_event_cannot_be_reentered(self):
        self.insert()
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE pm_trades SET status='closed'")
        self.assertIsNone(self.insert(id="102", conditionId="new", question="Will Bitcoin reach $80,000?"))

    def test_different_event_and_condition_can_enter(self):
        self.insert()
        self.assertIsNotNone(self.insert(id="102", conditionId="new", events=[{"id": "event-2"}],
                                         question="Will Bitcoin reach $80,000?"))

    def test_concurrent_insert_has_single_winner(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.insert(), range(12)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(self.count(), 1)

    def test_position_limit_is_not_double_counted(self):
        candidates = [market(id=str(i), conditionId=str(i), events=[{"id": str(i)}],
                             question=f"Will Bitcoin reach ${70000 + i}?") for i in range(8)]
        web = Mock(get=AsyncMock(return_value=response(candidates)))
        with patch.dict(pm.CONFIG, max_open_positions=4, max_per_cluster=10):
            n, detail = asyncio.run(pm._open_positions(web))
        self.assertEqual((n, len(detail), self.count()), (4, 4, 4))

    def test_exposure_limit_checks_new_position(self):
        with patch.dict(pm.CONFIG, max_exposure_pct=0.001):
            self.assertIsNone(self.insert())

    def test_reset_clears_only_simulation_and_preserves_backup(self):
        self.insert()
        puente.init_db()
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE predicciones (id INTEGER, texto TEXT)")
            c.execute("INSERT INTO predicciones VALUES (85,'inmutable')")
            c.execute("CREATE TABLE validation_audit (dato TEXT)")
            c.execute("INSERT INTO validation_audit VALUES ('provenance')")
            c.execute("INSERT INTO pm_senales_fund (trade_id) VALUES ('old')")
        pm._set_estado("bankroll", 6963.85)
        pm._set_estado("ultimo_escaneo", "old scan")
        result = self.reset()
        self.assertEqual(result["eliminados"]["pm_trades"], 1)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count("pm_senales_fund"), 0)
        self.assertEqual(self.count("pm_trade_keys"), 0)
        self.assertEqual(pm._bankroll(), 10000)
        self.assertIsNone(pm._get_estado("ultimo_escaneo"))
        self.assertNotEqual(pm._get_estado("generation"), self.generation)
        with sqlite3.connect(result["backup"]) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM pm_trades").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT * FROM predicciones").fetchall(), [(85, "inmutable")])
            self.assertEqual(c.execute("SELECT * FROM validation_audit").fetchall(), [("provenance",)])
        state = pm.resumen()
        self.assertEqual((state["n_abiertas"], state["n_cerradas"], state["pnl_total"]), (0, 0, 0))

    def test_existing_backup_aborts_without_deleting(self):
        self.insert()
        path = Path(self.temp.name) / "keep.db"
        path.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            pm.reset_paper(path)
        self.assertEqual(path.read_bytes(), b"keep")
        self.assertEqual(self.count(), 1)

    def test_inflight_download_cannot_repopulate_after_reset(self):
        async def get(*args, **kwargs):
            self.reset()
            return response([market()])
        self.assertEqual(asyncio.run(pm._open_positions(Mock(get=get))), (0, []))
        self.assertEqual(self.count(), 0)
        self.assertIsNone(self.insert())  # old generation

    def test_inflight_resolution_cannot_change_reset_balance(self):
        self.insert()
        async def get(*args, **kwargs):
            self.reset()
            return response(market(closed=True, outcomePrices='[1,0]'))
        self.assertEqual(asyncio.run(pm._check_resolutions(Mock(get=get))), 0)
        self.assertEqual(pm._bankroll(), 10000)

    def test_concurrent_resolutions_apply_pnl_once(self):
        self.insert()
        async def run():
            async def get(*args, **kwargs):
                await asyncio.sleep(0)  # ambos lectores ven inicialmente la misma posición
                return response(market(closed=True, outcomePrices='[1,0]'))
            return await asyncio.gather(pm._check_resolutions(Mock(get=get)), pm._check_resolutions(Mock(get=get)))
        self.assertEqual(sum(asyncio.run(run())), 1)
        with sqlite3.connect(self.path) as c:
            pnl = c.execute("SELECT pnl FROM pm_trades").fetchone()[0]
        self.assertAlmostEqual(pm._bankroll(), 10000 + pnl, places=2)

    def test_late_bridge_result_does_not_restore_deleted_trade(self):
        tid = self.insert()
        signal = dict(activo="BTC", nivel=70000, direccion="above", prob_fundamentada=.8,
                      prob_mercado=.9, divergencia_pp=-10, señal="neutral", fuente="fixture")
        puente.registrar_senal(tid, "101", signal)
        puente.registrar_senal(tid, "101", signal)
        self.assertEqual(self.count("pm_senales_fund"), 1)
        self.reset()
        puente.registrar_senal(tid, "101", signal)
        self.assertEqual(self.count("pm_senales_fund"), 0)

    def test_scanner_returns_new_count_and_is_idempotent_offline(self):
        client = AsyncMock()
        client.get.return_value = response([market(), market()])
        client.__aenter__.return_value = client
        async def run():
            with patch.object(pm.httpx, "AsyncClient", return_value=client):
                first = await pm.escanear()
                client.get.side_effect = [response(market()), response([market()])]
                second = await pm.escanear()
                return first, second
        first, second = asyncio.run(run())
        self.assertEqual((first["nuevas"], second["nuevas"], self.count()), (1, 0, 1))
        self.bridge.assert_awaited_once()

    def test_provider_error_does_not_open_positions(self):
        n, detail = asyncio.run(pm._open_positions(Mock(get=AsyncMock(return_value=response({}, 503)))))
        self.assertEqual((n, detail, self.count()), (0, "HTTP 503", 0))

    def test_dashboard_endpoint_after_reset(self):
        import main
        from fastapi.testclient import TestClient
        self.insert()
        self.reset()
        data = TestClient(main.app).get("/api/polymarket").json()
        self.assertEqual((data["bankroll"], data["n_abiertas"], data["n_cerradas"]), (10000, 0, 0))


if __name__ == "__main__":
    unittest.main()
