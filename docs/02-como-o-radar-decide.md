<a id="topo"></a>
<div align="right"><a href="../README.md">← voltar ao README</a></div>

# 🧠 02 — Como o radar decide

A tese em uma frase: **comprar o desconto de ativos líquidos, pouco diluídos e com protocolo em uso real** — e deixar uma IA conservadora dar a última palavra, sob supervisão do código.

O funil tem cinco camadas. Cada uma é mais cara que a anterior, então só o que sobrevive a uma chega à próxima.

## Índice

1. [Os dados](#1-os-dados)
2. [Filtros eliminatórios](#2-filtros-eliminatórios)
3. [O score de 0 a 100](#3-o-score-de-0-a-100)
4. [TVL do DefiLlama](#4-tvl-do-defillama)
5. [Binance: o mesmo ativo?](#5-binance-o-mesmo-ativo)
6. [A IA](#6-a-ia)
7. [A validação final](#7-a-validação-final)
8. [O que o radar não enxerga](#8-o-que-o-radar-não-enxerga)

---

## 1. Os dados

| Fonte | O que o radar lê | Chave |
|---|---|---|
| **CoinGecko** `/coins/markets` | Preço, market cap, volume 24h, FDV, variação de 1h/24h/7d/30d, máxima e mínima de 24h, preços horários de 7 dias, distância do topo histórico | Opcional (Demo grátis) |
| **DefiLlama** `/protocols` | TVL de cada protocolo, variação de 1d e 7d, categoria, chains | Não precisa |
| **Binance** `/api/v3/exchangeInfo` e `/ticker/24hr` | Pares spot negociáveis, último preço, volume | Não precisa |

Uma varredura faz **1 chamada** ao CoinGecko. A lista do DefiLlama fica em cache por 1 hora e a lista de pares da Binance por 6 horas.

> [!NOTE]
> Dado ausente continua ausente. Se o CoinGecko não informar a variação de 7d de um ativo, ele simplesmente não ganha os pontos de 7d — o radar nunca trata "sem dado" como "variação zero".

## 2. Filtros eliminatórios

Qualquer um destes tira o ativo da rodada, sem pontuação:

| Filtro | Regra |
|---|---|
| Stablecoins | Lista conhecida (USDT, USDC, USD1, RLUSD, GHO…) **e** detecção automática: preço entre US$ 0,97 e 1,03 com menos de 2% de variação em 7d e 3% em 30d |
| Embrulhados / staking | WBTC, WETH, stETH, weETH, cbBTC, JitoSOL e similares — eles só espelham outro ativo |
| Lastreados | Ouro e prata tokenizados (XAUT, PAXG…) e fundos de treasury (BUIDL, USDY…) |
| Tamanho | Market cap abaixo de `MIN_MARKET_CAP_USD` (US$ 100M) |
| Liquidez | Volume 24h abaixo de `MIN_DAILY_VOLUME_USD` (US$ 5M) |
| Diluição | FDV acima de `MAX_FDV_TO_MCAP_RATIO` × market cap (3×) — muito token ainda por destravar |

## 3. O score de 0 a 100

Todo ativo que passa começa com **35 pontos**:

| Critério | Condição | Pontos |
|---|---|---|
| 💧 Liquidez | Volume ÷ market cap ≥ 10% | **+15** |
| | Volume ÷ market cap ≥ 4% | +8 |
| 📉 Pullback | Variação de 7d entre −25% e −3% | **+16** |
| ➖ Consolidação | Variação de 7d entre −3% e +5% | +8 |
| 🧊 Não esticado | Variação de 30d entre −35% e +8% | +8 |
| 🔥 Esticado | Variação de 30d acima de +35% | **−18** |
| 🔄 Respiro | Variação de 24h abaixo de −5% | +6 |
| 🌡️ Superaquecido | Variação de 24h acima de +15% | **−12** |
| 🪙 Diluição baixa | FDV ÷ market cap ≤ 1,25 | **+12** |
| | FDV ÷ market cap ≤ 1,75 | +5 |

Sem DefiLlama, o máximo possível é 92.

## 4. TVL do DefiLlama

| Critério | Condição | Pontos |
|---|---|---|
| 🏗️ TVL relevante | TVL da família ≥ US$ 100M | +7 |
| 📈 TVL crescendo | Variação de 7d ≥ +8% | +8 |
| 📉 TVL encolhendo | Variação de 7d ≤ −10% | **−12** |

O difícil aqui não é a pontuação, é **parear o token certo com o protocolo certo**. Casar só pelo símbolo gera erros graves — na base real do DefiLlama, o símbolo `BNB` aponta para a "Binance CEX" (US$ 179 bilhões em reservas de corretora, não TVL de protocolo) e o `SOL` aponta para uma "Solana Farm" com US$ 258. Por isso o pareamento segue quatro regras:

1. **Corretoras (categoria CEX) são ignoradas.** Reserva de exchange não é uso de protocolo.
2. **`gecko_id` manda.** Se o protocolo declara o ID de outra moeda no CoinGecko, é colisão de ticker e fica de fora.
3. **Versões são somadas.** Aave V2 + V3 + V4 viram uma família só (via `parentProtocol`); a variação de 7d é ponderada pelo TVL de cada versão.
4. **Família pequena é descartada.** Abaixo de US$ 5M de TVL, o radar considera que não há dado de protocolo.

O DefiLlama não oferece variação de 1 mês nessa API — por isso os sinais usam 7 dias.

## 5. Binance: o mesmo ativo?

Os candidatos com **score ≥ 55** são ordenados do maior para o menor. O radar percorre essa fila e fica com os **5 primeiros** (`MAX_AI_CANDIDATES`) que:

- não foram alertados nas últimas 24h (cooldown — conferido **antes** da IA, para não gastar tokens);
- têm par `USDT` negociável no spot da Binance;
- têm **preço na Binance a no máximo 5% do preço do CoinGecko**.

A última regra existe por um motivo concreto: mesmo ticker pode ser outro token. No teste com dados reais, o `AI` do CoinGecko custava US$ 0,226 e o `AIUSDT` da Binance, US$ 0,0199 — ativos diferentes. Sem essa trava, o alerta falaria de um token e você compraria outro.

## 6. A IA

Cada candidato vai para a OpenAI com um retrato completo: preços, variações, faixas de 24h e 7d, diluição, score, motivos do score, contexto de TVL e preço da Binance. A resposta vem em **JSON validado por schema** (Structured Outputs), sempre com os mesmos campos:

| Campo | Conteúdo |
|---|---|
| `alert` | Vale alertar ou não |
| `confidence` | 0 a 100 |
| `risk` | baixo · médio · alto |
| `entry_min_usd` / `entry_max_usd` | Zona de entrada de referência |
| `target_usd` | Alvo de referência |
| `invalidation_usd` | Onde a tese deixa de valer |
| `reason` / `warning` | Justificativa e ressalva, em português |

As instruções pedem uma postura **conservadora**: usar só os dados recebidos, evitar ativos que já subiram na vertical, ancorar os níveis no preço da Binance e nas faixas de 24h/7d, manter a entrada perto do preço atual e, **se a evidência for fraca, não alertar**.

## 7. A validação final

A IA não tem a palavra final. Antes de virar mensagem, o código confere:

- `alert` é verdadeiro e `confidence` ≥ `MIN_CONFIDENCE_TO_ALERT` (65);
- **invalidação < entrada mínima ≤ entrada máxima < alvo**;
- a zona de entrada está a **no máximo 10%** do preço atual.

Resposta que falha em qualquer item é descartada e aparece no resumo do `/scan` como "descartado por níveis incoerentes". No `/analyze`, o problema é mostrado junto da análise.

## 8. O que o radar não enxerga

Saber os limites do filtro é o que separa usar o radar de ser usado por ele.

> [!WARNING]
> **Recuo e queda livre parecem iguais para o score.** Um ativo com −20% em 7d, −8% em 24h e −30% em 30d leva a pontuação máxima nesses critérios. O radar não olha médias móveis nem tendência longa — não sabe se é um pullback numa alta ou o começo de uma tendência de baixa.

| Ponto cego | Consequência |
|---|---|
| Regime de mercado | Se o BTC estiver desabando, o radar continua procurando "pullbacks" |
| Momentum passa pelo filtro | Liquidez, diluição e TVL somam pontos suficientes para um ativo com +27% em 30d chegar à IA — hoje é ela quem segura |
| Unlocks | FDV ÷ market cap é só um indicador indireto; o calendário de desbloqueios não é lido |
| Receita e fees | Um protocolo com TVL alta e receita zero pontua igual a um lucrativo |
| Derivativos, on-chain, notícias | Funding, open interest, fluxo de baleias e eventos não entram |
| Pesos arbitrários | Nenhum peso foi calibrado com histórico — não há evidência de que score 70 acerta mais que 55 |

Os dois próximos passos do [roadmap](../README.md#roadmap) atacam os mais graves: **filtro de regime pelo BTC** e **placar dos sinais** (medir se cada alerta bateu alvo ou invalidação).

<div align="right"><a href="#topo">▲ voltar ao topo</a> · <a href="03-operacao-e-deploy.md">Próximo: Operação e deploy →</a></div>
