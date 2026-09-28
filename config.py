import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()

def _int(name: str, default: int) -> int:
    # Empty values in .env ("VAR=") fall back to the default instead of crashing.
    raw = _str(name)
    return int(raw) if raw else default

def _float(name: str, default: float) -> float:
    raw = _str(name)
    return float(raw) if raw else default

def _bool(name: str, default: bool) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "sim"}

@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = _str("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = _str("TELEGRAM_CHAT_ID")
    openai_api_key: str = _str("OPENAI_API_KEY")
    openai_model: str = _str("OPENAI_MODEL", "gpt-5")

    coingecko_api_key: str = _str("COINGECKO_API_KEY")
    coingecko_base_url: str = _str("COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3")
    # Official market-data-only endpoint; api.binance.com also works.
    binance_base_url: str = _str("BINANCE_BASE_URL", "https://data-api.binance.vision")
    # Public USDⓈ-M futures data (funding, OI, long/short). No key needed.
    binance_futures_base_url: str = _str("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com")

    supabase_url: str = _str("SUPABASE_URL")
    supabase_service_role_key: str = _str("SUPABASE_SERVICE_ROLE_KEY")

    # Optional paid providers. Empty key = layer runs with free sources or stays unavailable.
    tokenomist_api_key: str = _str("TOKENOMIST_API_KEY")
    coinglass_api_key: str = _str("COINGLASS_API_KEY")
    lunarcrush_api_key: str = _str("LUNARCRUSH_API_KEY")
    newsdata_api_key: str = _str("NEWSDATA_API_KEY")

    enable_tokenomics: bool = _bool("ENABLE_TOKENOMICS", True)
    enable_derivatives: bool = _bool("ENABLE_DERIVATIVES", True)
    enable_social: bool = _bool("ENABLE_SOCIAL", True)
    enable_onchain: bool = _bool("ENABLE_ONCHAIN", False)
    enable_news: bool = _bool("ENABLE_NEWS", True)

    scan_interval_minutes: int = _int("SCAN_INTERVAL_MINUTES", 60)
    top_coins_to_scan: int = _int("TOP_COINS_TO_SCAN", 200)
    max_ai_candidates: int = _int("MAX_AI_CANDIDATES", 5)

    min_market_cap_usd: float = _float("MIN_MARKET_CAP_USD", 100_000_000)
    min_daily_volume_usd: float = _float("MIN_DAILY_VOLUME_USD", 5_000_000)
    max_fdv_to_mcap_ratio: float = _float("MAX_FDV_TO_MCAP_RATIO", 3.0)

    capital_brl: float = _float("CAPITAL_BRL", 20_000)
    max_position_pct: float = _float("MAX_POSITION_PCT", 3)

    # MIN_SCORE_TO_AI now applies to the composite score (weighted subscores).
    min_score_to_ai: float = _float("MIN_SCORE_TO_AI", 55)
    min_confidence_to_alert: int = _int("MIN_CONFIDENCE_TO_ALERT", 65)
    market_risk_off_min_confidence: int = _int("MARKET_RISK_OFF_MIN_CONFIDENCE", 80)
    critical_unlock_30d_pct: float = _float("CRITICAL_UNLOCK_30D_PCT", 10)
    alert_cooldown_hours: int = _int("ALERT_COOLDOWN_HOURS", 24)

    tracker_interval_minutes: int = _int("TRACKER_INTERVAL_MINUTES", 30)
    opportunity_expiry_days: int = _int("OPPORTUNITY_EXPIRY_DAYS", 14)

settings = Settings()
