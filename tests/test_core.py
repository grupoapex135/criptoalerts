"""
Regression tests. Run: python -m unittest discover -s tests -v
No network: every external API is mocked.
"""
import os
import sys
import unittest
from unittest import mock

# Deterministic settings regardless of the local .env (load_dotenv never overrides).
os.environ.update({
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "12345", "OPENAI_API_KEY": "test-key",
    "SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": "",
    "MIN_MARKET_CAP_USD": "100000000", "MIN_DAILY_VOLUME_USD": "5000000",
    "MAX_FDV_TO_MCAP_RATIO": "3", "MIN_SCORE_TO_AI": "55", "MIN_CONFIDENCE_TO_ALERT": "65",
    "MAX_AI_CANDIDATES": "5", "CAPITAL_BRL": "20000", "MAX_POSITION_PCT": "3",
    "ALERT_COOLDOWN_HOURS": "24",
})
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_analyzer  # noqa: E402
import radar  # noqa: E402
import telegram_app  # noqa: E402
from database import Database  # noqa: E402
from providers import DefiLlamaClient  # noqa: E402
from scoring import score_market_candidate, enrich_with_defi  # noqa: E402


def coin(**over):
    base = {
        "id": "pendle", "symbol": "pendle", "name": "Pendle", "current_price": 2.60,
        "market_cap": 500e6, "total_volume": 60e6, "fully_diluted_valuation": 600e6,
        "price_change_percentage_24h_in_currency": -2.0,
        "price_change_percentage_7d_in_currency": -8.0,
        "price_change_percentage_30d_in_currency": -5.0,
        "high_24h": 2.70, "low_24h": 2.50,
        "sparkline_in_7d": {"price": [2.4, 2.9, 2.6]},
    }
    base.update(over)
    return base


def ai(**over):
    base = {
        "alert": True, "confidence": 72, "risk": "medio",
        "entry_min_usd": 2.55, "entry_max_usd": 2.65, "target_usd": 3.10,
        "invalidation_usd": 2.35, "reason": "Pullback com TVL crescendo.", "warning": "Mercado volátil.",
    }
    base.update(over)
    return base


class ScoringTests(unittest.TestCase):
    def test_pullback_candidate_scores_above_ai_threshold(self):
        score, reasons = score_market_candidate(coin())
        self.assertGreaterEqual(score, 55)
        self.assertIn("7d pullback", reasons)

    def test_listed_and_unlisted_stablecoins_are_excluded(self):
        self.assertEqual(score_market_candidate(coin(symbol="usd1"))[0], 0)
        # Not in any list, but behaves like a USD peg.
        pegged = coin(symbol="newusd", current_price=0.9995,
                      price_change_percentage_7d_in_currency=0.02,
                      price_change_percentage_30d_in_currency=0.1)
        self.assertEqual(score_market_candidate(pegged)[0], 0)
        self.assertEqual(score_market_candidate(coin(symbol="paxg"))[0], 0)

    def test_missing_price_changes_do_not_earn_bonus(self):
        _, reasons = score_market_candidate(coin(
            price_change_percentage_7d_in_currency=None,
            price_change_percentage_30d_in_currency=None,
        ))
        self.assertNotIn("7d consolidation", reasons)
        self.assertNotIn("30d not extended", reasons)

    def test_tvl_contraction_penalizes(self):
        score, reasons = enrich_with_defi(60, {"tvl": 50e6, "change_7d": -15})
        self.assertEqual(score, 48)
        self.assertIn("TVL contracting 7d", reasons)


class DefiLlamaMatchingTests(unittest.TestCase):
    PROTOCOLS = [
        {"id": "1", "name": "Binance CEX", "symbol": "BNB", "category": "CEX", "tvl": 179e9, "change_7d": 1},
        {"id": "2", "name": "Aave V3", "symbol": "AAVE", "parentProtocol": "parent#aave",
         "category": "Lending", "tvl": 18e9, "change_7d": 10},
        {"id": "3", "name": "Aave V2", "symbol": "AAVE", "gecko_id": "aave", "parentProtocol": "parent#aave",
         "category": "Lending", "tvl": 2e9, "change_7d": -10},
        {"id": "4", "name": "Solana Farm", "symbol": "SOL", "category": "Dexs", "tvl": 258, "change_7d": 500},
        {"id": "5", "name": "PumpSwap", "symbol": "PUMP", "parentProtocol": "parent#pump",
         "category": "Dexs", "tvl": 394e6, "change_7d": 2},
        {"id": "6", "name": "PumpBTC", "symbol": "PUMP", "gecko_id": "pumpbtc",
         "category": "Bridge", "tvl": 900e6, "change_7d": 2},
    ]

    def setUp(self):
        self.llama = DefiLlamaClient()

    def test_cex_reserves_are_not_protocol_tvl(self):
        self.assertIsNone(self.llama.protocol_context("binancecoin", "BNB", self.PROTOCOLS))

    def test_protocol_versions_are_summed(self):
        ctx = self.llama.protocol_context("aave", "AAVE", self.PROTOCOLS)
        self.assertEqual(ctx["tvl"], 20e9)
        self.assertEqual(ctx["protocols_in_family"], 2)
        self.assertEqual(ctx["name"], "Aave V3")
        # 18B was 18/1.1, 2B was 2/0.9 -> family change ~ +7.6%
        self.assertAlmostEqual(ctx["change_7d"], 7.6, delta=0.1)

    def test_tiny_symbol_collision_is_ignored(self):
        self.assertIsNone(self.llama.protocol_context("solana", "SOL", self.PROTOCOLS))

    def test_gecko_id_of_other_coin_is_a_collision(self):
        ctx = self.llama.protocol_context("pump-fun", "PUMP", self.PROTOCOLS)
        self.assertEqual(ctx["name"], "PumpSwap")


class LevelChecksTests(unittest.TestCase):
    def test_coherent_levels_pass(self):
        self.assertEqual(radar.check_levels(ai(), 2.60), [])

    def test_inverted_levels_fail(self):
        self.assertTrue(radar.check_levels(ai(invalidation_usd=2.70), 2.60))
        self.assertTrue(radar.check_levels(ai(target_usd=2.50), 2.60))

    def test_far_away_entry_fails(self):
        problems = radar.check_levels(ai(entry_min_usd=1.9, entry_max_usd=2.0, invalidation_usd=1.8), 2.60)
        self.assertTrue(any("zona de entrada" in p for p in problems))

    def test_missing_level_fails(self):
        levels = ai()
        del levels["target_usd"]
        self.assertTrue(radar.check_levels(levels, 2.60))


class VenueTests(unittest.TestCase):
    def test_same_ticker_different_asset_is_rejected(self):
        # Real case: CoinGecko "AI" at $0.226 vs Binance AIUSDT at $0.0199.
        with mock.patch.object(radar.binance, "spot_pair", return_value="AIUSDT"), \
             mock.patch.object(radar.binance, "ticker", return_value={
                 "symbol": "AIUSDT", "last_price": 0.0199, "quote_volume": 1e6, "price_change_pct_24h": 1}):
            self.assertIsNone(radar.binance_venue("AI", 0.226))

    def test_matching_price_is_accepted(self):
        with mock.patch.object(radar.binance, "spot_pair", return_value="PENDLEUSDT"), \
             mock.patch.object(radar.binance, "ticker", return_value={
                 "symbol": "PENDLEUSDT", "last_price": 2.61, "quote_volume": 1e6, "price_change_pct_24h": 1}):
            self.assertIsNotNone(radar.binance_venue("PENDLE", 2.60))


class EvaluateTests(unittest.TestCase):
    VENUE = {"symbol": "PENDLEUSDT", "last_price": 2.60, "quote_volume": 1e6, "price_change_pct_24h": 1}

    def patches(self, coins, analyze_side_effect):
        return [
            mock.patch.object(radar.cg, "markets", return_value=coins),
            mock.patch.object(radar.llama, "protocols", return_value=[]),
            mock.patch.object(radar, "binance_venue", return_value=self.VENUE),
            mock.patch.object(radar, "analyze", side_effect=analyze_side_effect),
        ]

    def run_with(self, coins, analyze_side_effect, **kwargs):
        ps = self.patches(coins, analyze_side_effect)
        mocks = [p.start() for p in ps]
        self.addCleanup(lambda: [p.stop() for p in ps])
        return radar.evaluate_candidates(**kwargs), mocks[-1]

    def test_ai_failure_is_reported_not_silent(self):
        report, _ = self.run_with([coin()], RuntimeError("401 invalid key"))
        self.assertEqual(report["candidates"], 1)
        self.assertEqual(report["results"], [])
        self.assertIn("401", report["errors"][0])

    def test_incoherent_levels_are_rejected(self):
        report, _ = self.run_with([coin()], [ai(invalidation_usd=3.0)])
        self.assertEqual(report["results"], [])
        self.assertEqual(len(report["rejected"]), 1)

    def test_low_confidence_is_filtered(self):
        report, _ = self.run_with([coin()], [ai(confidence=50)])
        self.assertEqual(report["results"], [])

    def test_valid_alert_passes(self):
        report, _ = self.run_with([coin()], [ai()])
        self.assertEqual(len(report["results"]), 1)

    def test_cooldown_symbols_skip_the_ai(self):
        coins = [coin(), coin(id="uniswap", symbol="uni", name="Uniswap")]
        report, analyze_mock = self.run_with(coins, [ai()], skip_symbol=lambda s: s == "PENDLE")
        self.assertEqual(analyze_mock.call_count, 1)
        self.assertEqual(report["results"][0]["snapshot"]["symbol"], "UNI")


class DatabaseTests(unittest.TestCase):
    def test_cooldown_works_without_supabase(self):
        db = Database()
        self.assertFalse(db.enabled)
        self.assertFalse(db.recent_alert_exists("PENDLE"))
        db.save_alert(None, "pendle", "12345", "msg")
        self.assertTrue(db.recent_alert_exists("PENDLE"))


class AnalyzerTests(unittest.TestCase):
    def test_request_uses_strict_json_schema(self):
        fake = mock.MagicMock()
        fake.responses.create.return_value = mock.MagicMock(output_text='{"alert": false}', status="completed")
        with mock.patch.object(ai_analyzer, "_get_client", return_value=fake):
            self.assertEqual(ai_analyzer.analyze({"symbol": "BTC"}), {"alert": False})
        fmt = fake.responses.create.call_args.kwargs["text"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["strict"])
        self.assertEqual(set(fmt["schema"]["required"]), set(fmt["schema"]["properties"]))

    def test_empty_response_raises_clear_error(self):
        fake = mock.MagicMock()
        fake.responses.create.return_value = mock.MagicMock(output_text="", status="incomplete")
        with mock.patch.object(ai_analyzer, "_get_client", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                ai_analyzer.analyze({"symbol": "BTC"})


class TelegramTests(unittest.TestCase):
    def test_only_configured_chat_is_authorized(self):
        f = telegram_app._authorized_filter()
        self.assertEqual(f.chat_ids, frozenset({12345}))

    def test_opportunity_message(self):
        snapshot = radar.build_snapshot(coin(), 70, [], None, EvaluateTests.VENUE)
        msg = telegram_app.opportunity_message({"snapshot": snapshot, "ai": ai()})
        self.assertIn("OPORTUNIDADE — PENDLE", msg)
        self.assertIn("Binance (PENDLEUSDT)", msg)
        self.assertIn("R$ 600,00", msg)
        self.assertIn("Médio", msg)
        # mid 2.60: reward 0.50 / risk 0.25
        self.assertIn("Retorno/risco: 2,0", msg)
        self.assertIn("(+19.2%)", msg)

    def test_manual_message_shows_rejected_levels(self):
        snapshot = radar.build_snapshot(coin(), 70, [], None, None)
        msg = telegram_app.manual_analysis_message(
            {"snapshot": snapshot, "ai": ai(), "problems": ["níveis incoerentes"]})
        self.assertIn("NÃO PASSOU NO FILTRO", msg)
        self.assertIn("não confirmado na Binance", msg)

    def test_brl_format(self):
        self.assertEqual(telegram_app.brl(1234567.891), "R$ 1.234.567,89")


if __name__ == "__main__":
    unittest.main()
