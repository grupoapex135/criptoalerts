import logging
from telegram_app import build_app

if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    # httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    app = build_app()
    print("Crypto Radar iniciado.")
    app.run_polling(drop_pending_updates=True)
