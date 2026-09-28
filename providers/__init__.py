"""External data sources. Each client normalizes its payload and never decides."""
from providers.binance import BinanceClient
from providers.coingecko import CoinGeckoClient
from providers.defillama import DefiLlamaClient

__all__ = ["BinanceClient", "CoinGeckoClient", "DefiLlamaClient"]
