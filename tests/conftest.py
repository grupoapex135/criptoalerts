"""
Shared test setup. No test touches the network: every provider is mocked.
Env vars are fixed before `config` is imported, so a local .env never changes
test results (python-dotenv does not override existing variables).
"""
import os

os.environ.update({
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "12345", "OPENAI_API_KEY": "test-key",
    "SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": "",
    "TOKENOMIST_API_KEY": "", "COINGLASS_API_KEY": "", "LUNARCRUSH_API_KEY": "", "NEWSDATA_API_KEY": "",
    "ENABLE_TOKENOMICS": "true", "ENABLE_DERIVATIVES": "true", "ENABLE_SOCIAL": "true",
    "ENABLE_ONCHAIN": "false", "ENABLE_NEWS": "true",
    "MIN_MARKET_CAP_USD": "100000000", "MIN_DAILY_VOLUME_USD": "5000000",
    "MAX_FDV_TO_MCAP_RATIO": "3", "MIN_SCORE_TO_AI": "55", "MIN_CONFIDENCE_TO_ALERT": "65",
    "MARKET_RISK_OFF_MIN_CONFIDENCE": "80", "CRITICAL_UNLOCK_30D_PCT": "10",
    "MAX_AI_CANDIDATES": "5", "CAPITAL_BRL": "20000", "MAX_POSITION_PCT": "3",
    "ALERT_COOLDOWN_HOURS": "24", "OPPORTUNITY_EXPIRY_DAYS": "14",
})

from unittest import mock  # noqa: E402

import pytest  # noqa: E402
from providers.base import cache  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _no_network():
    """
    Blocks real HTTP. The fail-safe (`safe_call`) would swallow the error, so
    attempts are also recorded and fail the test at teardown.
    """
    attempts = []

    def blocked(url, *args, **kwargs):
        attempts.append(url)
        raise RuntimeError(f"network disabled in tests: {url}")
    with mock.patch("providers.base.requests.get", side_effect=blocked):
        yield
    assert not attempts, f"unmocked network calls: {attempts}"


def make_coin(**over):
    """A liquid, low-dilution coin in a 7d pullback (CoinGecko /coins/markets shape)."""
    base = {
        "id": "pendle", "symbol": "pendle", "name": "Pendle", "current_price": 2.60,
        "market_cap": 500e6, "total_volume": 60e6, "fully_diluted_valuation": 600e6,
        "circulating_supply": 170e6, "total_supply": 280e6, "max_supply": 281e6,
        "price_change_percentage_1h_in_currency": 0.1,
        "price_change_percentage_24h_in_currency": -2.0,
        "price_change_percentage_7d_in_currency": -8.0,
        "price_change_percentage_30d_in_currency": -5.0,
        "high_24h": 2.70, "low_24h": 2.50, "ath_change_percentage": -60.0,
        "sparkline_in_7d": {"price": [2.4, 2.9, 2.6]},
    }
    base.update(over)
    return base


def make_ai(**over):
    base = {
        "decision": "alert", "confidence": 80, "risk": "medio",
        "entry_min_usd": 2.55, "entry_max_usd": 2.65, "target_usd": 3.10,
        "invalidation_usd": 2.35, "short_reason": "Receita crescendo e mercado desalavancado.",
        "main_risk": "Mercado volátil.",
    }
    base.update(over)
    return base


def uptrend_closes(n=90, start=2.0, end=2.9):
    """Rising daily closes, so MA20 > MA50 and price above both."""
    step = (end - start) / (n - 1)
    return [start + i * step for i in range(n)]
