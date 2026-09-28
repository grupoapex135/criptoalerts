<a id="topo"></a>
<div align="right"><a href="../README.md">← voltar ao README</a></div>

# 🛠️ 01 — Instalação

Do zero ao primeiro alerta no Telegram. Leva uns 15 minutos, a maior parte criando contas.

## Índice

1. [Pré-requisitos](#1-pré-requisitos)
2. [Baixar e instalar](#2-baixar-e-instalar)
3. [Criar o bot no Telegram](#3-criar-o-bot-no-telegram)
4. [Chave da OpenAI](#4-chave-da-openai)
5. [Chave do CoinGecko (opcional, recomendada)](#5-chave-do-coingecko-opcional-recomendada)
6. [Supabase (opcional)](#6-supabase-opcional)
7. [Provedores opcionais](#7-provedores-opcionais)
8. [Testar o setup](#8-testar-o-setup)
9. [Descobrir o ID do chat ou grupo](#9-descobrir-o-id-do-chat-ou-grupo)
10. [Subir o radar](#10-subir-o-radar)
11. [Problemas comuns](#11-problemas-comuns)

---

## 1. Pré-requisitos

- **Python 3.10 ou mais novo** (testado com 3.11). No macOS: `brew install python@3.11`.
- Uma conta no **Telegram**.
- Uma conta na **OpenAI** com crédito — é o único serviço pago obrigatório.

## 2. Baixar e instalar

```bash
git clone https://github.com/grupoapex135/criptoalerts.git
cd criptoalerts

python3.11 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
cp .env.example .env
```

A partir daqui, todo o resto é preencher o `.env`.

## 3. Criar o bot no Telegram

1. No Telegram, abra o **[@BotFather](https://t.me/BotFather)**.
2. Mande `/newbot`, escolha um nome e um username (precisa terminar em `bot`).
3. Copie o token que ele devolve para o `.env`:

```env
TELEGRAM_BOT_TOKEN=1234567890:AAH...
```

> [!TIP]
> Quer os alertas num **grupo**? Crie o grupo, adicione o bot e promova-o a **administrador**. Assim ele enxerga as mensagens de texto livre (`analisa PENDLE`), não só os comandos.

Deixe `TELEGRAM_CHAT_ID` vazio por enquanto — ele é descoberto no passo 9.

## 4. Chave da OpenAI

Crie uma chave em [platform.openai.com/api-keys](https://platform.openai.com/api-keys):

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
```

O modelo precisa suportar **Structured Outputs** (`json_schema`). O radar usa isso para receber uma resposta sempre no mesmo formato.

## 5. Chave do CoinGecko (opcional, recomendada)

O CoinGecko responde mesmo sem chave, mas o limite público é baixo e costuma dar **erro 429** em horário de pico. O plano **Demo é gratuito** e sem cartão:

1. Acesse [coingecko.com/en/api/pricing](https://www.coingecko.com/en/api/pricing) → **Create Demo Account**.
2. No **Developer Dashboard**, clique em **+ Add New Key**.
3. Cole no `.env` (não mude a `COINGECKO_BASE_URL`):

```env
COINGECKO_API_KEY=CG-...
```

Por varredura: 4 páginas de mercado (top 1000), 1 de dados globais, o histórico dos ~15 melhores de cada modo (12h em cache) e os perfis dos finalistas (24h em cache). Com o placar Pré-Binance, a estimativa é de ~7 mil chamadas por mês no intervalo padrão, abaixo das 10 mil do plano Demo — **com a chave**. Sem chave, o limite público não aguenta os dois modos.

## 6. Supabase (opcional)

Sem Supabase o radar funciona igual, mas o **placar dos sinais**, a **watchlist** e o cooldown ficam só em memória e zeram quando o bot reinicia. Para acompanhar resultados de verdade, configure.

1. Crie um projeto em [supabase.com](https://supabase.com/).
2. **SQL Editor** → cole o conteúdo de [`schema.sql`](../schema.sql) → **Run**.
3. Em **Project Settings → API**, copie:

```env
SUPABASE_URL=https://xxxx.supabase.co
# chave secreta: service_role ou sb_secret_...
SUPABASE_SERVICE_ROLE_KEY=
```

> [!WARNING]
> No `.env`, comentário vai **sempre na linha de cima**. `CHAVE=   # texto` com valor vazio faz o texto do comentário virar o valor da chave.

> [!CAUTION]
> A chave secreta ignora todas as regras de acesso do banco. Ela só existe no `.env` do servidor — nunca em frontend, print ou repositório. A **senha do banco** não é usada pelo bot e não precisa estar no `.env`.

O `schema.sql` liga o RLS nas tabelas sem nenhuma política: só a chave secreta consegue ler e escrever.

**Já usa o Supabase de uma versão anterior?** Rode o `schema.sql` inteiro de novo. Ele é idempotente e só aditivo: cria o que falta (status do sinal, subnotas, dossiê, watchlist) e não apaga nem reescreve nenhum dado. Sinais antigos entram no placar como abertos.

## 7. Provedores opcionais

Nenhum é obrigatório. Sem chave, cada camada usa a fonte grátis ou fica sem dados — e o radar continua funcionando.

| Variável | Provedor | O que acrescenta | Plano |
|---|---|---|---|
| `TOKENOMIST_API_KEY` | [Tokenomist](https://tokenomist.ai/) | unlocks futuros (7d/30d, destinatários, cronograma) | Pro ou acima (~1.000 req/mês) |
| `COINGLASS_API_KEY` | [CoinGlass](https://www.coinglass.com/pricing) | derivativos de várias exchanges + liquidações (sem ela: Binance Futures) | Hobbyist ou acima |
| `LUNARCRUSH_API_KEY` | [LunarCrush](https://lunarcrush.com/developers) | menções, engajamento, sentimento | Individual ou acima — **o plano grátis gera chave, mas a API responde 402** |
| `NEWSDATA_API_KEY` | [NewsData.io](https://newsdata.io/) | manchetes classificadas (hack, delisting, listing, burn…) | tem plano grátis (com ~12h de atraso) |

Cada camada também tem um interruptor (`ENABLE_TOKENOMICS`, `ENABLE_DERIVATIVES`, `ENABLE_SOCIAL`, `ENABLE_ONCHAIN`, `ENABLE_NEWS`). Os ritmos de chamada e os caches já respeitam os limites desses planos.

O **modo Pré-Binance** vem ligado (`ENABLE_PRE_LISTING=true`) e não precisa de chave: usa CoinGecko, Binance Futures e GoPlus, todos grátis. A faixa de market cap, o volume mínimo e o teto de IA desse modo ficam no bloco "Modo Pré-Binance" do `.env.example`.

## 8. Testar o setup

```bash
pip install -r requirements-dev.txt
pytest                      # regras do radar, sem internet
python smoke_test.py --ai   # APIs reais + uma análise completa do BTC na OpenAI
```

O smoke test confere cada fonte e mostra o que está ligado. Resultado esperado:

```text
1) CoinGecko...                  OK — 10 moedas · histórico BTC com 91 dias
2) Regime de mercado...          OK — risk_on: BTC -2.0% em 7d e +7.8% em 30d, ...
3) DefiLlama (TVL, fees, hacks)  OK — 8395 protocolos · AAVE TVL $19.0B · fees 30d $36.7M
4) Binance spot...               OK — spot BTCUSDT a $83,486
5) Binance Futures...            OK — funding 0.0063% · OI 24h -1.43% · L/S 1.3452
6) Provedores opcionais...       Tokenomist: sem chave (...)  ·  CoinGlass: sem chave (...)
7) Telegram...                   OK — bot @SeuBot · TELEGRAM_CHAT_ID: VAZIO — rode o bot e mande /id
8) Supabase...                   desligado (opcional)
9) Análise completa com IA...    OK — 👀 OBSERVAR — BTC ...

Tudo certo. Depois rode: python main.py
```

Sem o `--ai`, ele pula a chamada paga.

## 9. Descobrir o ID do chat ou grupo

1. Rode `python main.py`.
2. No chat privado com o bot **ou no grupo**, mande `/id` (em grupo com vários bots: `/id@SeuBot`).
3. Copie o número. **Grupo tem ID negativo** (ex.: `-1001234567890`) — copie com o sinal.
4. Pare o bot com `Ctrl+C` e cole no `.env`:

```env
TELEGRAM_CHAT_ID=-1001234567890
```

Enquanto esse campo estiver vazio, o bot fica em **modo configuração**: responde só a `/start` e `/id`, e a varredura automática fica desligada.

## 10. Subir o radar

```bash
python main.py
```

Em segundos chega no chat: **🟢 Crypto Radar iniciado**. A primeira varredura começa 15 segundos depois — a coleta de dados leva menos de um minuto e a IA mais alguns, porque cada finalista é analisado em sequência. O acompanhamento dos sinais roda a cada 30 minutos. Mande `/status` para ver o placar e a saúde do radar.

> [!IMPORTANT]
> O radar só funciona enquanto esse processo estiver rodando. Fechou o terminal ou o computador dormiu, parou. Para 24/7, veja [Operação e deploy](03-operacao-e-deploy.md).

## 11. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `429 Too Many Requests` do CoinGecko | Limite público estourado | Configure a chave Demo (passo 5) |
| `451` da Binance | Servidor hospedado nos EUA | Hospede em outra região (ex.: São Paulo, Europa) |
| `Conflict: terminated by other getUpdates request` | Duas cópias do bot com o mesmo token | Deixe só uma rodando |
| Telegram: token recusado | Token copiado errado ou revogado | Gere de novo no @BotFather (`/token`) |
| Bot não responde no grupo | `TELEGRAM_CHAT_ID` diferente do grupo | Mande `/id` no grupo e confira o número, com o sinal |
| `/scan` mostra "Erros da IA" | Chave OpenAI inválida, sem crédito ou modelo indisponível | Rode `python smoke_test.py --ai` para ver o erro exato |
| `TELEGRAM_CHAT_ID deve ser numérico` | Algo além do número no `.env` | Deixe só os dígitos (e o `-` do grupo) |
| Smoke test: `schema desatualizado` | Supabase criado com a versão anterior | Rode o `schema.sql` de novo (não apaga dados) |
| Log: `Tokenomist indisponível` (ou CoinGlass, LunarCrush…) | Chave errada, plano sem acesso ao endpoint ou limite do plano | A análise segue sem a camada; confira a chave e o plano |
| `/status`: `⚠️ sem acesso no plano` · smoke: `plano sem acesso a este endpoint` | A chave existe, mas o plano não inclui a API (HTTP 401/402/403) | O provedor se desliga sozinho; faça upgrade do plano ou apague a chave do `.env` |
| `/status` mostra fonte ⚪ | Chave vazia ou camada desligada no `.env` | Esperado se você não usa aquele provedor |

<div align="right"><a href="#topo">▲ voltar ao topo</a> · <a href="02-como-o-radar-decide.md">Próximo: Como o radar decide →</a></div>
