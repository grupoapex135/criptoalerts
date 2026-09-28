# Crypto Radar MVP

Radar privado de oportunidades cripto, operado **100% pelo Telegram** (sem dashboard).

## O que faz

1. Busca os 200 maiores ativos no CoinGecko (preço, variações, faixa de 7 dias).
2. Elimina stablecoins, wrappers, ativos lastreados (ouro/RWA), baixa liquidez, market cap pequeno e FDV exagerado.
3. Busca contexto de protocolo/TVL no DefiLlama quando existe correspondência confiável.
4. Ordena por score e confirma, só para os melhores, que o ativo é negociado no spot da Binance **e que é o mesmo ativo** (preço Binance × CoinGecko).
5. Envia até `MAX_AI_CANDIDATES` para a OpenAI, com resposta em JSON estruturado.
6. Valida os níveis da IA no código (invalidação < entrada < alvo, entrada perto do preço atual).
7. Se passar, manda o alerta no Telegram e aplica cooldown por ativo.
8. Opcionalmente grava oportunidades e alertas no Supabase.

**Não executa ordens.** As faixas de entrada, alvo e invalidação são referências de pesquisa geradas sobre os dados fornecidos e não garantias de retorno.

## Estrutura

```text
criptoalerts/
├── main.py            # entrada: logging + bot
├── config.py          # lê o .env
├── providers.py       # CoinGecko, DefiLlama, Binance
├── scoring.py         # pré-filtros e score quantitativo
├── radar.py           # pipeline: varredura → IA → validação
├── ai_analyzer.py     # OpenAI (Structured Outputs)
├── database.py        # Supabase + cooldown em memória
├── telegram_app.py    # comandos, job agendado, /status
├── smoke_test.py      # checagem de setup
├── tests/test_core.py # testes de regressão (sem rede)
├── schema.sql
├── requirements.txt
└── .env.example
```

## 1. Criar o bot no Telegram

No Telegram, abra **@BotFather**:

1. `/newbot`
2. escolha nome e username
3. copie o token para `TELEGRAM_BOT_TOKEN`

Para descobrir seu Chat ID:

1. deixe `TELEGRAM_CHAT_ID` vazio;
2. rode o bot (`python main.py`);
3. mande `/id` (ou `/start`);
4. copie o número para `TELEGRAM_CHAT_ID` no `.env`;
5. reinicie o bot.

**Só esse chat** consegue usar `/scan`, `/analyze` e `/status`. Qualquer outra pessoa que achar o bot recebe "Bot privado." — isso protege seus créditos da OpenAI. Para usar num grupo, coloque o ID do grupo (número negativo).

## 2. OpenAI

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5
```

O modelo precisa suportar Structured Outputs (`json_schema`).

## 3. CoinGecko

Se a API pública limitar (erro 429), crie uma chave Demo gratuita e coloque em `COINGECKO_API_KEY`.

## 4. Supabase (opcional)

Sem Supabase o radar funciona, mas o cooldown fica só em memória (zera se o bot reiniciar) e não há histórico.

1. Crie um projeto no Supabase.
2. SQL Editor → cole `schema.sql` → execute.
3. Copie:
   - Project URL → `SUPABASE_URL`
   - chave secreta (`service_role` ou `sb_secret_...`) → `SUPABASE_SERVICE_ROLE_KEY`

O `schema.sql` liga RLS nas tabelas: só a chave secreta (o bot) lê e escreve. Essa chave **nunca** vai para frontend ou repositório.

## 5. Instalar

Requer Python 3.10+ (testado com 3.11).

```bash
python3.11 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
cp .env.example .env
```

Preencha o `.env`.

## 6. Testar

```bash
python -m unittest discover -s tests   # regras do radar, sem rede
python smoke_test.py                   # APIs públicas + token do Telegram + Supabase
python smoke_test.py --ai              # + uma análise real de BTC na OpenAI
```

## 7. Rodar

```bash
python main.py
```

Ao subir, o bot manda "🟢 Crypto Radar iniciado" no seu chat. Comandos:

```text
/scan            força uma varredura agora
/analyze PENDLE  analisa um ativo do top 200
/status          saúde do radar: última varredura, erros, próxima execução
/id              mostra o chat ID
```

Também funciona escrever: `analisa PENDLE`.

Se algo quebrar (CoinGecko fora, chave da OpenAI inválida, Binance bloqueada), o bot avisa no chat — no máximo uma vez a cada 3 horas.

## 8. Deixar rodando 24/7

O radar só varre enquanto o processo está vivo. No Mac, ele para quando o computador dorme. Para rodar direto, use um servidor (VPS, Railway, Render, Fly.io) com `python main.py` como processo contínuo.

- **Uma instância por token.** Duas cópias com o mesmo token dão erro `Conflict` no Telegram.
- **Região fora dos EUA.** A Binance bloqueia acesso a partir dos EUA (HTTP 451). Prefira São Paulo ou Europa. O endpoint usado é o de dados públicos (`BINANCE_BASE_URL`), sem chave.

## Lógica do radar

Pré-filtros:

- market cap mínimo, volume diário mínimo, FDV/market cap máximo;
- exclui stablecoins (lista + detecção de ativo preso em ~US$1), wrappers/staking e ativos lastreados (ouro, fundos de treasury);
- procura pullback/consolidação em vez de comprar pumps;
- dado ausente (ex.: variação de 7d nula) não soma ponto.

DefiLlama:

- ignora corretoras (a "TVL" da Binance CEX são reservas, não protocolo);
- casa pelo `gecko_id` ou pelo símbolo, descartando protocolos cujo `gecko_id` aponta para outra moeda;
- soma as versões do mesmo protocolo (Aave V2 + V3 + V4...);
- ignora famílias com TVL abaixo de US$ 5M (colisões de ticker);
- usa variação de TVL em 7 dias (a API não fornece 1 mês).

Binance:

- exige par `USDT` spot negociável;
- descarta se o preço da Binance divergir mais de 5% do CoinGecko — mesmo ticker, ativo diferente (caso real: `AI` e `FRAX`).

IA:

- recebe só os melhores candidatos, o que limita custo;
- a resposta é validada no código antes de virar alerta; níveis incoerentes são descartados e aparecem no resumo do `/scan`.

## Ajustes no `.env`

```env
SCAN_INTERVAL_MINUTES=60
TOP_COINS_TO_SCAN=200
MAX_AI_CANDIDATES=5

MIN_MARKET_CAP_USD=100000000
MIN_DAILY_VOLUME_USD=5000000
MAX_FDV_TO_MCAP_RATIO=3

CAPITAL_BRL=20000
MAX_POSITION_PCT=3

MIN_SCORE_TO_AI=55
MIN_CONFIDENCE_TO_ALERT=65
ALERT_COOLDOWN_HOURS=24
```

`CAPITAL_BRL` e `MAX_POSITION_PCT` são limites definidos por você.
A IA não decide qual porcentagem do seu patrimônio deve ser investida.

## Limitações desta V0

- O casamento token ↔ protocolo do DefiLlama é heurístico e pode não existir.
- Ainda não há Tokenomist/unlocks especializados.
- Ainda não há Nansen/Glassnode/whales.
- Ainda não há Bybit.
- Ainda não há backtesting.
- A zona de entrada é uma referência heurística/IA, não execução.
- O radar não deve ser tratado como garantia ou recomendação financeira automática.

O próximo upgrade útil é adicionar **unlocks + receita/fees + histórico de sinais**, antes de adicionar mais exchanges.
