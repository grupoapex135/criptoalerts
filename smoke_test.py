"""
Setup check. Usage:
  python smoke_test.py        # public APIs + Telegram token + Supabase
  python smoke_test.py --ai   # also runs one real analysis (BTC) on OpenAI
"""
import sys
import requests
from providers import CoinGeckoClient, DefiLlamaClient, BinanceClient
from config import settings

failures = []

def step(title, fn):
    print(title)
    try:
        fn()
    except Exception as exc:
        failures.append(title)
        print(f"   FALHOU — {exc}")

def coingecko():
    coins = CoinGeckoClient().markets(10)
    print(f"   OK — {len(coins)} moedas. Ex.: {coins[0]['symbol'].upper()}")

def defillama():
    llama = DefiLlamaClient()
    protocols = llama.protocols()
    ctx = llama.protocol_context("aave", "AAVE", protocols)
    tvl = f"${ctx['tvl'] / 1e9:.1f}B" if ctx else "sem contexto"
    print(f"   OK — {len(protocols)} protocolos. AAVE TVL (família): {tvl}")

def binance():
    b = BinanceClient()
    pair = b.spot_pair("BTC")
    print(f"   OK — BTC spot: {pair} ({settings.binance_base_url})")
    print(f"   ticker: {b.ticker(pair) if pair else None}")

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
        print("   desligado (opcional) — cooldown fica só em memória")
        return
    db.client.table("alerts").select("id").limit(1).execute()
    db.client.table("opportunities").select("id").limit(1).execute()
    print("   OK — tabelas alerts e opportunities acessíveis")

def openai_analysis():
    from radar import analyze_symbol
    from telegram_app import manual_analysis_message
    result = analyze_symbol("BTC")
    print("   OK — resposta da IA:\n")
    print("   " + manual_analysis_message(result).replace("\n", "\n   "))

def main():
    step("1) CoinGecko...", coingecko)
    step("2) DefiLlama...", defillama)
    step("3) Binance...", binance)
    step("4) Telegram...", telegram)
    step("5) Supabase...", supabase)
    if "--ai" in sys.argv:
        step(f"6) OpenAI ({settings.openai_model})...", openai_analysis)

    print("\nConfig:")
    print(f"   scan interval: {settings.scan_interval_minutes} min")
    print(f"   max position limit: R$ {settings.capital_brl * settings.max_position_pct / 100:.2f}")

    if failures:
        print(f"\n{len(failures)} verificação(ões) falharam: {', '.join(failures)}")
        sys.exit(1)
    print("\nTudo certo. Depois rode: python main.py")

if __name__ == "__main__":
    main()
