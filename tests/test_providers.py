"""Provider normalization and fail-safe behaviour. All HTTP is mocked."""
from datetime import datetime, timezone
from unittest import mock

import pytest
import requests

import ai_analyzer
from providers.base import ProviderError, TTLCache, http_get_json, safe_call
from providers.binance_futures import BinanceFuturesClient
from providers.coinglass import CoinGlassClient
from providers.defillama import DefiLlamaClient
from providers.lunarcrush import LunarCrushClient
from providers.news import NewsClient
from providers.tokenomist import TokenomistClient


# ------------------------------------------------------------------ base
class TestBase:
    def test_retry_then_raise_provider_error(self):
        with mock.patch("providers.base.requests.get", side_effect=requests.ConnectionError("down")) as get, \
             mock.patch("providers.base.time.sleep"):
            with pytest.raises(ProviderError):
                http_get_json("https://x.test/api", retries=2)
        assert get.call_count == 3

    def test_4xx_does_not_retry(self):
        resp = mock.MagicMock(status_code=401)
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
        with mock.patch("providers.base.requests.get", return_value=resp) as get:
            with pytest.raises(ProviderError, match="HTTP 401"):
                http_get_json("https://x.test/api")
        assert get.call_count == 1

    def test_safe_call_turns_failure_into_default(self):
        def boom():
            raise ProviderError("offline")
        assert safe_call("Tokenomist", boom, default=None) is None

    def test_cache_hits_within_ttl(self):
        c, calls = TTLCache(), []
        fetch = lambda: calls.append(1) or "v"  # noqa: E731
        assert c.get_or_set("k", 60, fetch) == "v"
        assert c.get_or_set("k", 60, fetch) == "v"
        assert len(calls) == 1


# ------------------------------------------------------------------ DefiLlama
class TestDefiLlamaMatching:
    PROTOCOLS = [
        {"id": "1", "name": "Binance CEX", "symbol": "BNB", "category": "CEX", "tvl": 179e9, "change_7d": 1},
        {"id": "2", "name": "Aave V3", "symbol": "AAVE", "parentProtocol": "parent#aave",
         "category": "Lending", "tvl": 18e9, "change_7d": 10, "chainTvls": {"borrowed": 12e9}},
        {"id": "3", "name": "Aave V2", "symbol": "AAVE", "gecko_id": "aave", "parentProtocol": "parent#aave",
         "category": "Lending", "tvl": 2e9, "change_7d": -10},
        {"id": "4", "name": "Solana Farm", "symbol": "SOL", "category": "Dexs", "tvl": 258, "change_7d": 500},
        {"id": "5", "name": "PumpSwap", "symbol": "PUMP", "parentProtocol": "parent#pump",
         "category": "Dexs", "tvl": 394e6, "change_7d": 2},
        {"id": "6", "name": "PumpBTC", "symbol": "PUMP", "gecko_id": "pumpbtc",
         "category": "Bridge", "tvl": 900e6, "change_7d": 2},
    ]

    def setup_method(self):
        self.llama = DefiLlamaClient()

    def test_cex_reserves_are_not_protocol_tvl(self):
        assert self.llama.protocol_context("binancecoin", "BNB", self.PROTOCOLS) is None

    def test_protocol_versions_are_summed(self):
        ctx = self.llama.protocol_context("aave", "AAVE", self.PROTOCOLS)
        assert ctx["tvl"] == 20e9
        assert ctx["protocols_in_family"] == 2
        assert ctx["borrowed"] == 12e9
        assert ctx["slug"] == "aave"
        assert ctx["change_7d"] == pytest.approx(7.6, abs=0.1)

    def test_tiny_symbol_collision_is_ignored(self):
        assert self.llama.protocol_context("solana", "SOL", self.PROTOCOLS) is None

    def test_gecko_id_of_other_coin_is_a_collision(self):
        assert self.llama.protocol_context("pump-fun", "PUMP", self.PROTOCOLS)["name"] == "PumpSwap"

    def test_fees_summed_across_family_with_growth(self):
        ctx = self.llama.protocol_context("aave", "AAVE", self.PROTOCOLS)
        fees = [
            {"defillamaId": "2", "parentProtocol": "parent#aave", "total30d": 60, "total60dto30d": 50, "total7d": 15},
            {"defillamaId": "3", "parentProtocol": "parent#aave", "total30d": 40, "total60dto30d": 30, "total7d": 10},
            {"defillamaId": "99", "parentProtocol": "parent#other", "total30d": 999, "total60dto30d": 1},
        ]
        m = self.llama.flow_metrics(ctx, fees)
        assert m == {"total_30d": 100.0, "total_7d": 25.0, "growth_30d": 25.0}

    def test_chain_context_and_chain_fees(self):
        chains = [{"name": "Solana", "gecko_id": "solana", "tokenSymbol": "SOL", "tvl": 6.5e9}]
        ctx = self.llama.chain_context("solana", chains)
        assert ctx["kind"] == "chain" and ctx["slug"] == "Solana"
        fees = [{"defillamaId": "chain#solana", "name": "Solana", "total30d": 24e6, "total60dto30d": 20e6}]
        assert self.llama.flow_metrics(ctx, fees)["growth_30d"] == 20.0

    def test_recent_hacks_match_by_id_and_name(self):
        ctx = self.llama.protocol_context("aave", "AAVE", self.PROTOCOLS)
        hacks = [
            {"name": "Aave V3", "date": 2000, "defillamaId": None, "amount": 1e6},
            {"name": "Other", "date": 2000, "defillamaId": "2", "amount": 2e6},
            {"name": "Aave V2", "date": 10, "defillamaId": "3"},  # too old
            {"name": "Aavegotchi", "date": 2000, "defillamaId": None},  # different project
        ]
        found = self.llama.recent_hacks(ctx, "Aave", since_ts=1000, hacks=hacks)
        assert [h["name"] for h in found] == ["Aave V3", "Other"]


# ------------------------------------------------------------------ derivatives
class TestDerivativesProviders:
    def test_binance_futures_normalize(self):
        oi = [{"sumOpenInterestValue": str(100 + i)} for i in range(25)]  # 100..124
        d = BinanceFuturesClient.normalize(
            {"lastFundingRate": "0.0001"}, oi, [{"longShortRatio": "0.85"}], {"quoteVolume": "5e7"})
        assert d["funding_rate_pct"] == 0.01
        assert d["open_interest_usd"] == 124
        assert d["oi_change_24h"] == 24.0
        assert d["oi_change_1h"] == round((124 / 123 - 1) * 100, 2)
        assert d["long_short_ratio"] == 0.85
        assert d["liquidations_24h_usd"] is None

    def test_binance_futures_without_perp_returns_none(self):
        client = BinanceFuturesClient()
        with mock.patch.object(client, "_get", side_effect=ProviderError("x -> HTTP 400")):
            assert client.derivatives("NOPERP") is None

    def test_coinglass_normalize_uses_all_row(self):
        oi = [{"exchange": "Binance", "open_interest_usd": 1},
              {"exchange": "All", "open_interest_usd": 5e8, "open_interest_change_percent_24h": -18.5,
               "open_interest_change_percent_1h": 0.3, "open_interest_change_percent_4h": -2}]
        d = CoinGlassClient.normalize(oi, [{"close": "0.0071"}], [{"exchange": "All", "liquidation_usd": 3e6}],
                                      [{"global_account_long_short_ratio": 1.4}])
        assert d["open_interest_usd"] == 5e8 and d["oi_change_24h"] == -18.5
        assert d["funding_rate_pct"] == 0.0071
        assert d["liquidations_24h_usd"] == 3e6
        assert d["long_short_ratio"] == 1.4

    def test_coinglass_disabled_without_key(self):
        assert CoinGlassClient(api_key="").enabled is False


# ------------------------------------------------------------------ tokenomics
class TestTokenomist:
    TOKENS = [{"id": "pendle", "symbol": "PENDLE", "circulatingSupply": 170e6},
              {"id": "fake-pendle", "symbol": "PENDLE", "circulatingSupply": 1e3}]

    def test_resolve_prefers_slug_then_supply(self):
        assert TokenomistClient.resolve(self.TOKENS, "pendle", "PENDLE", None)["id"] == "pendle"
        assert TokenomistClient.resolve(self.TOKENS[1:] + self.TOKENS[:1], "x", "PENDLE", 160e6)["id"] == "pendle"
        assert TokenomistClient.resolve(self.TOKENS[1:], "x", "PENDLE", 160e6) is None

    def test_normalize_windows_and_recipients(self):
        now = datetime(2026, 9, 28, tzinfo=timezone.utc)
        events = [
            {"unlockDate": "2026-10-01T00:00:00Z", "cliffUnlocks": {"cliffAmount": 1.7e6, "cliffValue": 4e6,
             "allocationBreakdown": [{"standardAllocationName": "Founder / Team"}]}},
            {"unlockDate": "2026-10-20T00:00:00Z", "cliffUnlocks": {"cliffAmount": 8.5e6,
             "allocationBreakdown": [{"standardAllocationName": "Private Investors"}]}},
            {"unlockDate": "2026-09-01T00:00:00Z", "cliffUnlocks": {"cliffAmount": 9e9}},  # past
        ]
        u = TokenomistClient.normalize(events, circulating=170e6, now=now)
        assert u["unlock_7d_pct"] == 1.0
        assert u["unlock_30d_pct"] == 6.0
        assert u["next_unlock_date"] == "2026-10-01"
        assert u["unlock_recipients"] == ["Founder / Team", "Private Investors"]
        assert len(u["vesting_schedule"]) == 2


# ------------------------------------------------------------------ social + news
class TestSocialAndNews:
    def test_lunarcrush_24h_vs_previous_24h(self):
        series = [{"time": i, "posts_active": 10 if i < 24 else 15, "interactions": 100,
                   "contributors_active": 7} for i in range(48)]
        s = LunarCrushClient.normalize({"sentiment": 72, "social_dominance": 0.4, "galaxy_score": 61}, series)
        assert s["mentions_24h"] == 360
        assert s["mentions_change_24h"] == 50.0
        assert s["engagement_change_24h"] == 0.0
        assert s["sentiment"] == 72

    def test_news_grouped_by_coin(self):
        payload = {"results": [
            {"title": "PENDLE listing on X", "coin": ["pendle"], "pubDate": "2026-09-27 10:00:00", "source_name": "S"},
            {"title": "Market wrap", "coin": ["btc", "pendle"], "pubDate": "2026-09-27 11:00:00"},
        ]}
        g = NewsClient.normalize(payload, ["PENDLE", "AAVE"])
        assert [a["title"] for a in g["PENDLE"]] == ["PENDLE listing on X", "Market wrap"]
        assert g["AAVE"] == []

    def test_news_key_goes_in_header_not_url(self):
        client = NewsClient(api_key="SECRET")
        with mock.patch("providers.news.http_get_json", return_value={"results": []}) as get:
            client.headlines(["PENDLE"])
        _, kwargs = get.call_args
        assert "SECRET" not in str(kwargs["params"])
        assert kwargs["headers"] == {"X-ACCESS-KEY": "SECRET"}


# ------------------------------------------------------------------ AI contract
class TestAnalyzer:
    def test_request_uses_strict_json_schema(self):
        fake = mock.MagicMock()
        fake.responses.create.return_value = mock.MagicMock(output_text='{"decision": "reject"}', status="completed")
        with mock.patch.object(ai_analyzer, "_get_client", return_value=fake):
            assert ai_analyzer.analyze({"asset": {"symbol": "BTC"}}) == {"decision": "reject"}
        fmt = fake.responses.create.call_args.kwargs["text"]["format"]
        assert fmt["type"] == "json_schema" and fmt["strict"] is True
        assert set(fmt["schema"]["required"]) == set(fmt["schema"]["properties"])
        assert fmt["schema"]["properties"]["decision"]["enum"] == ["alert", "watch", "reject"]

    def test_empty_response_raises_clear_error(self):
        fake = mock.MagicMock()
        fake.responses.create.return_value = mock.MagicMock(output_text="", status="incomplete")
        with mock.patch.object(ai_analyzer, "_get_client", return_value=fake):
            with pytest.raises(RuntimeError, match="incomplete"):
                ai_analyzer.analyze({})


# ------------------------------------------------------------------ fixes found on live data
def test_growth_from_a_newly_tracked_protocol_is_unknown():
    # Real case: BONK family fees showed +2056% in 30d because tracking had just started.
    llama = DefiLlamaClient()
    ctx = {"kind": "protocol", "member_ids": ["7"], "parent_key": None}
    m = llama.flow_metrics(ctx, [{"defillamaId": "7", "total30d": 7.6e6, "total60dto30d": 3.5e5}])
    assert m["total_30d"] == 7.6e6
    assert m["growth_30d"] is None


def test_binance_futures_falls_back_to_1000x_contract():
    # Real case: PEPE and BONK only trade as 1000PEPEUSDT / 1000BONKUSDT perpetuals.
    client = BinanceFuturesClient()
    calls = []

    def fake_get(path, params):
        calls.append(params["symbol"])
        if params["symbol"] == "PEPEUSDT":
            raise ProviderError("x -> HTTP 400")
        return {"/fapi/v1/premiumIndex": {"lastFundingRate": "0.0001"},
                "/futures/data/openInterestHist": [{"sumOpenInterestValue": "10"}] * 25,
                "/futures/data/globalLongShortAccountRatio": [{"longShortRatio": "1.2"}],
                "/fapi/v1/ticker/24hr": {"quoteVolume": "1e8"}}[path]

    with mock.patch.object(client, "_get", side_effect=fake_get):
        d = client.derivatives("PEPE")
    assert d["funding_rate_pct"] == 0.01
    assert calls[0] == "PEPEUSDT" and "1000PEPEUSDT" in calls


def test_cache_fetches_once_under_concurrent_misses():
    import threading
    import time as _time
    c, calls = TTLCache(), []

    def slow_fetch():
        calls.append(1)
        _time.sleep(0.05)
        return "v"

    threads = [threading.Thread(target=lambda: c.get_or_set("k", 60, slow_fetch)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


def test_bulk_tickers_fall_back_when_a_pair_was_delisted():
    from providers.binance import BinanceClient
    client = BinanceClient()
    ok = {"symbol": "UNIUSDT", "lastPrice": "8", "quoteVolume": "1", "priceChangePercent": "0"}

    def fake(url, params=None, **kw):
        if "symbols" in params:
            raise ProviderError("ticker -> HTTP 400")
        if params["symbol"] == "GONEUSDT":
            raise ProviderError("ticker -> HTTP 400")
        return ok

    with mock.patch("providers.binance.http_get_json", side_effect=fake):
        out = client.tickers(["UNIUSDT", "GONEUSDT"])
    assert list(out) == ["UNIUSDT"] and out["UNIUSDT"]["last_price"] == 8.0


def test_family_with_a_sibling_pointing_to_another_coin_is_a_collision():
    # Real case: coin "Velo" (Stellar) got Velodrome's TVL and revenue — both use the VELO ticker,
    # Velodrome's versions have no gecko_id, only the parent entry does.
    protocols = [
        {"id": "10", "name": "Velodrome V2", "symbol": "VELO", "parentProtocol": "parent#velodrome",
         "category": "Dexs", "tvl": 40e6},
        {"id": "11", "name": "Velodrome V3", "symbol": "VELO", "parentProtocol": "parent#velodrome",
         "category": "Dexs", "tvl": 30e6},
        {"id": "12", "name": "Velodrome", "symbol": "VELO", "gecko_id": "velodrome-finance",
         "parentProtocol": "parent#velodrome", "category": "Dexs", "tvl": 0},
    ]
    llama = DefiLlamaClient()
    assert llama.protocol_context("velo", "VELO", protocols) is None
    assert llama.protocol_context("velodrome-finance", "VELO", protocols)["tvl"] == 70e6


def test_exact_gecko_id_beats_ticker_matches():
    # Real data (Sep/2026): Velodrome versions have no gecko_id; "Velo Finance" declares gecko_id "velo".
    protocols = [
        {"id": "1799", "name": "Velodrome V3", "symbol": "VELO", "parentProtocol": "parent#velodrome",
         "category": "Dexs", "tvl": 20e6},
        {"id": "3302", "name": "Velodrome V2", "symbol": "VELO", "parentProtocol": "parent#velodrome",
         "category": "Dexs", "tvl": 17e6},
        {"id": "900", "name": "Velo Finance", "symbol": "VELO", "gecko_id": "velo", "category": "Dexs", "tvl": 80e3},
    ]
    llama = DefiLlamaClient()
    assert llama.protocol_context("velo", "VELO", protocols) is None           # its own protocol is too small
    assert llama.protocol_context("velodrome-finance", "VELO", protocols)["tvl"] == 37e6


def test_paid_provider_switches_off_when_the_plan_has_no_access():
    # Real case: a valid LunarCrush key on the free plan returns HTTP 402 on every call.
    client = LunarCrushClient(api_key="k")
    with mock.patch("providers.lunarcrush.http_get_json", side_effect=ProviderError("x -> HTTP 402")) as get, \
         mock.patch("providers.lunarcrush._limiter"):
        with pytest.raises(ProviderError):
            client.coins()
        assert client.enabled is False and "402" in client.blocked
    assert get.call_count == 1


def test_transient_errors_do_not_switch_the_provider_off():
    client = NewsClient(api_key="k")
    with mock.patch("providers.news.http_get_json", side_effect=ProviderError("x -> HTTP 503")):
        with pytest.raises(ProviderError):
            client.headlines(["BTC"])
    assert client.enabled is True
