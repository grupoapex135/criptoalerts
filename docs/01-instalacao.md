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
7. [Testar o setup](#7-testar-o-setup)
8. [Descobrir o ID do chat ou grupo](#8-descobrir-o-id-do-chat-ou-grupo)
9. [Subir o radar](#9-subir-o-radar)
10. [Problemas comuns](#10-problemas-comuns)

---

## 1. Pré-requisitos

- **Python 3.10 ou mais novo** (testado com 3.11). No macOS: `brew install python@3.11`.
- Uma conta no **Telegram**.
- Uma conta na **OpenAI** com crédito — é o único serviço pago do projeto.

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

Deixe `TELEGRAM_CHAT_ID` vazio por enquanto — ele é descoberto no passo 8.

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

O radar gasta **1 chamada por varredura** e 1 por `/analyze` — cerca de 720 por mês no intervalo padrão, bem abaixo das 10 mil do plano Demo.

## 6. Supabase (opcional)

Sem Supabase o radar funciona igual, mas o cooldown fica só em memória (zera quando o bot reinicia) e não há histórico de sinais.

1. Crie um projeto em [supabase.com](https://supabase.com/).
2. **SQL Editor** → cole o conteúdo de [`schema.sql`](../schema.sql) → **Run**.
3. Em **Project Settings → API**, copie:

```env
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=      # chave secreta: service_role ou sb_secret_...
```

> [!CAUTION]
> A chave secreta ignora todas as regras de acesso do banco. Ela só existe no `.env` do servidor — nunca em frontend, print ou repositório. A **senha do banco** não é usada pelo bot e não precisa estar no `.env`.

O `schema.sql` liga o RLS nas tabelas sem nenhuma política: só a chave secreta consegue ler e escrever.

## 7. Testar o setup

```bash
python smoke_test.py --ai
```

Ele confere, um por um, CoinGecko, DefiLlama, Binance, o token do Telegram, o Supabase (se configurado) e roda **uma análise real do BTC** na OpenAI. Resultado esperado:

```text
1) CoinGecko...  OK — 10 moedas. Ex.: BTC
2) DefiLlama...  OK — 8395 protocolos. AAVE TVL (família): $19.0B
3) Binance...    OK — BTC spot: BTCUSDT
4) Telegram...   OK — bot @SeuBot · TELEGRAM_CHAT_ID: VAZIO — rode o bot e mande /id
5) Supabase...   desligado (opcional)
6) OpenAI...     OK — resposta da IA: ...

Tudo certo. Depois rode: python main.py
```

Sem o `--ai`, ele pula a chamada paga.

## 8. Descobrir o ID do chat ou grupo

1. Rode `python main.py`.
2. No chat privado com o bot **ou no grupo**, mande `/id` (em grupo com vários bots: `/id@SeuBot`).
3. Copie o número. **Grupo tem ID negativo** (ex.: `-1001234567890`) — copie com o sinal.
4. Pare o bot com `Ctrl+C` e cole no `.env`:

```env
TELEGRAM_CHAT_ID=-1001234567890
```

Enquanto esse campo estiver vazio, o bot fica em **modo configuração**: responde só a `/start` e `/id`, e a varredura automática fica desligada.

## 9. Subir o radar

```bash
python main.py
```

Em segundos chega no chat: **🟢 Crypto Radar iniciado**. A primeira varredura começa 15 segundos depois — com o `gpt-5`, ela leva alguns minutos, porque cada candidato é analisado em sequência. Mande `/status` para acompanhar.

> [!IMPORTANT]
> O radar só funciona enquanto esse processo estiver rodando. Fechou o terminal ou o computador dormiu, parou. Para 24/7, veja [Operação e deploy](03-operacao-e-deploy.md).

## 10. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `429 Too Many Requests` do CoinGecko | Limite público estourado | Configure a chave Demo (passo 5) |
| `451` da Binance | Servidor hospedado nos EUA | Hospede em outra região (ex.: São Paulo, Europa) |
| `Conflict: terminated by other getUpdates request` | Duas cópias do bot com o mesmo token | Deixe só uma rodando |
| Telegram: token recusado | Token copiado errado ou revogado | Gere de novo no @BotFather (`/token`) |
| Bot não responde no grupo | `TELEGRAM_CHAT_ID` diferente do grupo | Mande `/id` no grupo e confira o número, com o sinal |
| `/scan` mostra "Erros da IA" | Chave OpenAI inválida, sem crédito ou modelo indisponível | Rode `python smoke_test.py --ai` para ver o erro exato |
| `TELEGRAM_CHAT_ID deve ser numérico` | Algo além do número no `.env` | Deixe só os dígitos (e o `-` do grupo) |

<div align="right"><a href="#topo">▲ voltar ao topo</a> · <a href="02-como-o-radar-decide.md">Próximo: Como o radar decide →</a></div>
