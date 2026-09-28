"""
Setup check against the real APIs. Usage:
  python smoke_test.py        # public APIs + optional providers + Telegram token + Supabase
  python smoke_test.py --ai   # also runs one real full analysis (BTC) on OpenAI
"""
import sys
import requests
from config import settings

failures = []
SECRETS = [v for v in (settings.telegram_bot_token, settings.openai_api_key, settings.coingecko_api_key,
                       settings.supabase_service_role_key, settings.tokenomist_api_key, settings.coinglass_api_key,
                       settings.lunarcrush_api_key, settings.newsdata_api_key) if v]

def redact(text: str) -> str:
    # requests puts the URL in connection errors, and the Telegram URL carries the bot token.
    for secret in SECRETS:
        text = text.replace(secret, "***")
    return text

def step(title, fn, required=True):
    print(title)
    try:
        fn()
    except Exception as exc:
        if required:
            failures.append(title)
        print(f"   {'FALHOU' if required else 'indisponível'} — {redact(str(exc))}")

def coingecko():
    from providers import CoinGeckoClient
    cg = CoinGeckoClient()
    coins = cg.markets(10)
    history = cg.daily_history("bitcoin")
    print(f"   OK — {len(coins)} moedas · histórico BTC com {len(history['prices'])} dias")

def regime():
    import radar
    r = radar.get_market_regime(radar.cg.markets(settings.top_coins_to_scan))
    print(f"   OK — {r['status']}: {r['reason']}")

def defillama():
    from providers import DefiLlamaClient
    llama = DefiLlamaClient()
    protocols = llama.protocols()
    ctx = llama.protocol_context("aave", "AAVE", protocols)
    fees = llama.flow_metrics(ctx, llama.overview("fees")) if ctx else None
    print(f"   OK — {len(protocols)} protocolos · AAVE TVL ${ctx['tvl'] / 1e9:.1f}B · "
          f"fees 30d ${((fees or {}).get('total_30d') or 0) / 1e6:.1f}M · {len(llama.hacks())} hacks na base")

def binance():
    from providers import BinanceClient
    b = BinanceClient()
    pair = b.spot_pair("BTC")
    print(f"   OK — spot {pair} a ${b.ticker(pair)['last_price']:,.0f} ({settings.binance_base_url})")

def binance_futures():
    from providers.binance_futures import BinanceFuturesClient
    d = BinanceFuturesClient().derivatives("BTC")
    print(f"   OK — funding {d['funding_rate_pct']}% · OI 24h {d['oi_change_24h']}% · L/S {d['long_short_ratio']}")

def optional_providers():
    import radar
    # One real, cheap call per configured key: a key can be valid while its plan has no API access.
    probes = (
        ("Tokenomist", radar.tokenomist, lambda: radar.tokenomist.token_list()),
        ("CoinGlass", radar.coinglass, lambda: radar.coinglass.derivatives("BTC")),
        ("LunarCrush", radar.lunarcrush, lambda: radar.lunarcrush.coins()),
        ("NewsData", radar.news, lambda: radar.news.headlines(["BTC"])),
    )
    for name, client, probe in probes:
        if not client.api_key:
            print(f"   {name}: sem chave (camada usa fonte grátis ou fica sem dados)")
            continue
        try:
            probe()
            print(f"   {name}: OK — chave com acesso")
        except Exception as exc:
            reason = "plano sem acesso a este endpoint" if client.blocked else "falhou"
            print(f"   {name}: ⚠️ {reason} — {redact(str(exc))[:160]}")
    print(f"   On-chain: {'ligado' if 'onchain' in radar.enabled_layers() else 'desligado'}")

def telegram():
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN vazio")
    r = requests.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe", timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"token recusado ({data.get('description')})")
    chat = settings.telegram_chat_id or "VAZIO — rode o bot e mande /id"
    print(f"   OK — bot @{data['result']['username']} · TELEGRAM_CHAT_ID: {chat}")

def supabase():
    from database import Database
    db = Database()
    if not db.enabled:
        print("   desligado (opcional) — placar, cooldown e watchlist ficam só em memória")
        return
    db.client.table("alerts").select("id").limit(1).execute()
    try:
        db.client.table("opportunities").select("id,status,scores,dossier,mode,coin_id,binance_listed_at").limit(1).execute()
        db.client.table("watchlist").select("symbol").limit(1).execute()
    except Exception as exc:
        raise RuntimeError(f"schema desatualizado — rode schema.sql de novo no SQL Editor ({exc})") from exc
    print("   OK — tabelas alerts, opportunities (v3) e watchlist acessíveis")

def openai_analysis():
    import radar
    from telegram_app import manual_analysis_message
    result = radar.analyze_symbol("BTC")
    print("   OK — análise completa:\n")
    print("   " + manual_analysis_message(result).replace("\n", "\n   "))

def main():
    step("1) CoinGecko...", coingecko)
    step("2) Regime de mercado...", regime)
    step("3) DefiLlama (TVL, fees, hacks)...", defillama)
    step("4) Binance spot...", binance)
    step("5) Binance Futures (derivativos grátis)...", binance_futures, required=False)
    step("6) Provedores opcionais...", optional_providers)
    step("7) Telegram...", telegram)
    step("8) Supabase...", supabase)
    if "--ai" in sys.argv:
        step(f"9) Análise completa com IA ({settings.openai_model})...", openai_analysis)

    print("\nConfig:")
    print(f"   varredura a cada {settings.scan_interval_minutes} min · acompanhamento a cada "
          f"{settings.tracker_interval_minutes} min · sinais expiram em {settings.opportunity_expiry_days} dias")
    print(f"   limite por posição: R$ {settings.capital_brl * settings.max_position_pct / 100:.2f}")

    if failures:
        print(f"\n{len(failures)} verificação(ões) falharam: {', '.join(failures)}")
        sys.exit(1)
    print("\nTudo certo. Depois rode: python main.py")

if __name__ == "__main__":
    main()
