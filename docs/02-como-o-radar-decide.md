<a id="topo"></a>
<div align="right"><a href="../README.md">← voltar ao README</a></div>

# 🧠 02 — Como o radar decide

A tese: **uma oportunidade é a convergência de evidências independentes** — mercado, tendência, fundamentos, tokenomics, alavancagem, comportamento on-chain, social e eventos — e não um preço que caiu. O código calcula tudo que dá para calcular; a IA só interpreta, tenta derrubar a tese e decide.

Todos os números abaixo são padrões e ficam em [`analysis/params.py`](../analysis/params.py) (os mais usados também no `.env`).

## Índice

1. [O funil](#1-o-funil)
2. [Regime de mercado](#2-regime-de-mercado)
3. [Qualidade de mercado e filtros](#3-qualidade-de-mercado-e-filtros)
4. [Tendência](#4-tendência)
5. [Fundamentos](#5-fundamentos)
6. [Tokenomics e captura de valor](#6-tokenomics-e-captura-de-valor)
7. [Derivativos](#7-derivativos)
8. [Social e on-chain](#8-social-e-on-chain)
9. [Catalisadores](#9-catalisadores)
10. [Nota final e cobertura](#10-nota-final-e-cobertura)
11. [Vetos](#11-vetos)
12. [A IA e a decisão final](#12-a-ia-e-a-decisão-final)
13. [Modo Pré-Binance](#13-modo-pré-binance)

---

## 1. O funil

| Estágio | Ativos | O que acontece |
|---|---|---|
| **Filtros** | top 200 → ~90 | Elimina stablecoins, embrulhados, lastreados, market cap < US$ 100M, volume < US$ 5M, FDV > 3× market cap e quem não tem par `USDT` na Binance |
| **Estágio 1 — lote** | ~90 | Subnotas com dados que chegam em lote (CoinGecko + datasets inteiros do DefiLlama). Tendência ainda pelo fallback de 7d/30d |
| **Cooldown** | — | Ativo alertado nas últimas 24h sai aqui, antes de qualquer chamada por ativo |
| **Estágio 2 — histórico** | 15 | Preço na Binance conferido contra o CoinGecko (±5%; mesmo ticker pode ser outro token) e histórico de 90 dias: tendência real e crescimento da oferta |
| **Estágio 3 — profundo** | 8 + watchlist | Unlocks, derivativos, social, on-chain, hacks, manchetes e TVL de 30 dias, em paralelo |
| **IA** | até 5 | Só quem não tem veto, tem nota ≥ mínima do regime e cobertura ≥ 50% |

A watchlist (`/watch`) garante que um ativo passe pelos estágios 2 e 3 mesmo fora do corte — mas ele ainda precisa passar nos filtros e nos vetos.

## 2. Regime de mercado

Lido uma vez por varredura (cache de 5 min), antes de qualquer ativo:

| Dado | Fonte |
|---|---|
| BTC preço, 24h, 7d, 30d; ETH 7d, 30d | CoinGecko `/coins/markets` |
| ETH/BTC e sua variação em 30d | calculado das variações de 30d de ambos |
| Market cap total e variação 24h | CoinGecko `/global` |
| BTC vs. média de 50 dias | histórico diário do BTC |
| Fear & Greed Index | alternative.me (grátis) — só contexto, não muda o status |

| Status | Regra | Efeito |
|---|---|---|
| `risk_off` | BTC ≤ −10% em 7d **ou** ≤ −20% em 30d **ou** mercado total ≤ −7% em 24h **ou** BTC caindo no curto (7d ≤ −5%) e no médio prazo (30d ≤ −10% ou ≥5% abaixo da MA50) | confiança mínima = `MARKET_RISK_OFF_MIN_CONFIDENCE` (80), nota mínima +10, limite por posição ×0,5, veto para ativo em baixa/capitulação |
| `risk_on` | BTC em alta no médio prazo (30d ≥ +5% e acima da MA50), sem queda no curto e sem dia de pânico | critérios normais |
| `neutral` | o resto | critérios normais |

## 3. Qualidade de mercado e filtros

Subnota começa em 50: volume ÷ market cap ≥ 10% **+25** (≥ 4% +15, < 1% −20) · market cap ≥ US$ 1 bi **+15** (≥ 300M +5) · volume ≥ US$ 50M **+10**.

Stablecoins são detectadas por lista **e** por comportamento (preço entre US$ 0,97 e 1,03 com menos de 2% de variação em 7d e 3% em 30d). Ouro tokenizado, fundos de treasury e tokens embrulhados/de staking também saem.

## 4. Tendência

Com o histórico diário de 220 dias (cache de 12h):

| Estado | Regra |
|---|---|
| **CAPITULAÇÃO** | ≥ 25% abaixo da MA50 e (7d ≤ −15% ou ≥ 35% abaixo da máxima de 30d) |
| **BAIXA** | preço < MA50 com MA20 < MA50, ou ≥ 12% abaixo da MA50 |
| **ALTA** | preço > MA50 com MA20 > MA50 |
| **LATERAL** | o resto |

| Situação | Subnota |
|---|---|
| Alta com **recuo saudável** (abaixo da MA20 ou 7d ≤ −3%) | 85 |
| Alta sem recuo | 70 |
| Lateral na parte de baixo da faixa de 30d | 65 (senão 55) |
| Baixa · Capitulação | 25 · 20 |
| **Esticado** (≥ 15% acima da MA20, 30d > +35% ou 24h > +15%) | −25 |
| **Parabólico** (≥ 30% acima da MA20 ou ≥ 50% acima da MA50) | no máximo 20 |

Sem histórico, a tendência sai das variações de 7d/30d (`fallback_7d_30d`) e a subnota fica limitada a 70.

Para a leitura de ciclo, o mesmo histórico dá — sem chamada extra — a **MA200**, o cruzamento **MA50 × MA200** (cruz de ouro/morte nos últimos 10 dias), o **RSI de 14 períodos diário e semanal** e o **preço em BTC**: quanto o ativo está abaixo da própria máxima de 200+ dias *contra o Bitcoin*. Esses dados vão para a IA e para a mensagem; não mudam a subnota de tendência. A cruz de ouro no gráfico **semanal** exigiria ~4 anos de histórico, e o plano grátis do CoinGecko entrega 1 ano.

## 5. Fundamentos

Do DefiLlama, para o **protocolo** (soma de todas as versões — Aave V2+V3+V4) ou, se não houver, para a **blockchain** do token:

| Métrica | Origem |
|---|---|
| TVL, variação 7d | `/protocols` (ou `/v2/chains`) |
| TVL 30d | histórico do protocolo/chain — só no estágio profundo |
| Fees e receita 7d/30d e crescimento 30d × 30d anteriores | `/overview/fees` (receita = o que o protocolo retém) |
| Receita repassada a holders | `/overview/fees?dataType=dailyHoldersRevenue` |
| Volume DEX 30d | `/overview/dexs` |
| Valor emprestado | `chainTvls.borrowed` |
| Market cap ÷ receita anual | calculado |

Protocolo que acabou de entrar na base mostra crescimento de +2.000%; acima de 10× o período anterior, o crescimento vira `None` (histórico insuficiente, não uso real).

**Divergências** que somam pontos: `price_down_revenue_up` (30d ≤ −10% com receita +10%), `price_down_usage_up` (fees ou volume +10%), `price_flat_tvl_up` (±8% com TVL +10%). A que tira: `price_up_revenue_down`.

Subnota começa em 50 e sobe/desce com TVL (tamanho e tendência), crescimento de fees e receita, receita para holders, múltiplo de receita (≤ 15× +10; ≥ 300× −5) e divergências. **Token sem presença no DefiLlama fica sem esta camada** — não recebe nota baixa.

## 6. Tokenomics e captura de valor

| Dado | Fonte |
|---|---|
| Oferta circulante, total e máxima; % em circulação; FDV ÷ market cap | CoinGecko |
| Crescimento real da oferta em 30d | market cap ÷ preço, hoje vs. 30 dias atrás |
| Unlocks em 7 e 30 dias (% da oferta circulante), próximo unlock, destinatários, cronograma | Tokenomist (com chave) |
| Captura de valor | receita para holders no DefiLlama |

**Risco de diluição** (% da oferta circulante liberada em 30 dias): < 2% baixo · 2–5% médio · 5–10% alto · ≥ `CRITICAL_UNLOCK_30D_PCT` (10%) crítico. Vale o **pior** entre o cronograma de unlocks (Tokenomist) e o crescimento real da oferta — que também captura emissão linear. `dilution_basis` diz qual dos dois decidiu.

**Captura de valor** só é marcada com fonte estruturada (receita repassada a holders > 0). Sem ela: `{"available": false}` — "desconhecido", não "não captura".

## 7. Derivativos

Fonte: CoinGlass (multi-exchange, com liquidações) se houver chave; senão Binance Futures, grátis. Moedas de preço baixo são negociadas como contrato 1000× (`1000PEPEUSDT`) e o radar tenta esse par automaticamente.

| Posicionamento | Regra | Subnota |
|---|---|---|
| **desalavancado** | OI 24h ≤ −15%, funding perto de zero, preço corrigiu em 7d | 80 |
| **saudável** | nenhum extremo | 65 |
| **vendidos demais** | funding ≤ −0,03% ou long/short ≤ 0,5 | 55 |
| **comprados demais** | funding ≥ 0,05%, ou OI +25% com preço +8% em 24h, ou long/short ≥ 3 | 20 |
| funding ≥ 0,10% | — | 10 |

## 8. Social e on-chain

**Social** (LunarCrush): compara as últimas 24h com as 24h anteriores. *Emergente* (menções +20%) vale 70; *quieto* 50; *em alta* (+60%) 55; *eufórico* (+200% com sentimento ≥ 80% ou engajamento +200%) **20** — euforia é risco.

**On-chain**: interface pronta ([`providers/onchain.py`](../providers/onchain.py)). Um provedor devolve fluxo para exchanges, variação de reservas, atividade de baleias e acumulação de grandes carteiras; a camada classifica em *acumulação* (75), *neutro* (50) ou *distribuição* (25).

## 9. Catalisadores

Só para os finalistas do estágio profundo:

- **Hacks** do DefiLlama nos últimos 30 dias (grátis) → risco crítico.
- **Manchetes** (NewsData.io, com chave) classificadas por palavras-chave, sem IA: *crítico* (hack, exploit, drenado, delisting, rug, insolvência, saques pausados, depeg) · *negativo* (processo, SEC, investigação, banimento, instabilidade, unlock) · *positivo* (listing, mainnet, upgrade, buyback, burn, parceria, aquisição, integração, aprovação/ETF).

Manchete **informa, não veta**: a marcação por moeda vem do provedor e pode ser sobre outro ativo ("Binance vai deslistar ABC/BTC" marcada como BTC). Só o dado estruturado de hacks bloqueia.

## 10. Nota final e cobertura

```text
nota final = média ponderada das subnotas DISPONÍVEIS
             mercado 15 · tendência 15 · fundamentos 25 · tokenomics 20 · derivativos 10 · on-chain 10 · social 5
             (+ sinais de listagem 15, só no modo Pré-Binance)
cobertura  = peso das camadas com dado ÷ peso das camadas com fonte configurada
```

- Camada sem dado **sai da média** (não vira zero) e reduz a **cobertura**.
- Camada sem fonte configurada (ex.: social sem chave da LunarCrush) não conta contra a cobertura.
- A confiança da IA perde até **20 pontos** proporcionalmente à cobertura que faltou.
- Abaixo de **50% de cobertura** o ativo não vai para a IA.

**Risco** (0–100, separado da média) soma regime, tendência ruim, esticado/parabólico, diluição, FDV alto, alavancagem, euforia, eventos negativos e volatilidade. Ele define o risco mínimo mostrado: a IA não consegue chamar de "baixo" algo que o código mede como alto.

## 11. Vetos

Bloqueiam o alerta independentemente da média e aparecem com código e explicação:

| Código | Regra |
|---|---|
| `critical_unlock` | unlock em 30d ≥ `CRITICAL_UNLOCK_30D_PCT` (cronograma do Tokenomist) |
| `critical_supply_inflation` | oferta real cresceu ≥ esse mesmo % em 30d (vale mesmo com Tokenomist: emissão linear só aparece aqui) |
| `low_liquidity` | volume < mínimo ou < 0,5% do market cap |
| `critical_event` | hack ou exploit recente no protocolo (DefiLlama) |
| `risk_off_downtrend` | mercado em `risk_off` e ativo em baixa ou capitulação |
| `extreme_dilution` | FDV ÷ market cap > `MAX_FDV_TO_MCAP_RATIO` |
| `overheated_leverage` | preço +15% em 24h, OI +40% e funding ≥ 0,05% |
| `contract_risk` | contrato com risco grave na GoPlus: honeypot, não deixa vender tudo, taxa de venda > 10%, taxa alterável, dono oculto, dono que altera saldos ou retoma o controle; na Solana, freeze, saldo alterável, intransferível |
| `already_on_binance` | *(Pré-Binance)* o CoinGecko mostra um mercado na Binance — o ativo já está lá com outro ticker (caso real: BTT/BTTC) |

No `/analyze`, um ativo vetado **não chama a IA**: a resposta mostra os motivos e para ali.

## 12. A IA e a decisão final

A IA recebe o **dossiê** — `asset` (com descrição e categorias oficiais), `market_regime`, `market`, `venue`, `trend`, `vs_btc`, `fundamentals`, `tokenomics`, `derivatives`, `onchain`, `social`, `catalysts`, `scores`, `vetoes` — e é instruída a:

1. tentar derrubar a tese primeiro;
2. listar os riscos reais;
3. avaliar se há assimetria;
4. decidir **alert**, **watch** ou **reject** (evidência insuficiente = reject);
5. sugerir níveis só se fizer sentido, sem recalcular números e sem inventar dado ausente;
6. escrever a **tese e utilidade** (o que o projeto faz e por que isso tem uso real) **apenas** a partir da descrição e das categorias oficiais — sem parcerias, clientes ou notícias inventadas;
7. escrever um **plano de referência** (ex.: entradas fracionadas, o que esperar) e o principal risco.

A resposta vem em JSON validado por schema. Aí o código decide:

| Condição | Resultado |
|---|---|
| Qualquer veto | rejeitado |
| IA disse `alert`, níveis coerentes (invalidação < entrada ≤ entrada máx. < alvo, invalidação abaixo e alvo acima do preço atual, entrada a até 10% dele), confiança ajustada ≥ mínima do regime e nota ≥ mínima do regime | **🟢 alerta** |
| IA disse `alert` mas confiança ou nota abaixo da régua | 👀 observação |
| IA disse `alert` com níveis incoerentes | descartado (aparece no resumo do `/scan`) |
| IA disse `watch` / `reject` | 👀 observação / rejeitado |

Só o alerta vira mensagem e sinal acompanhado no [placar](03-operacao-e-deploy.md#3-placar-dos-sinais).

## 13. Modo Pré-Binance

O objetivo é achar ativos **com fundamento antes de uma listagem no spot da Binance**. A Binance não publica o que vai listar; o radar trabalha com indícios públicos e mede o resultado.

**Universo:** top `PRE_LISTING_TOP_N` (1000) do CoinGecko, **fora** do spot da Binance, market cap de US$ 10M a US$ 1B, volume ≥ US$ 250 mil. Saem stablecoins (inclusive de outras moedas: JPY, CHF, EUR), embrulhados, staking, ouro e **ações/ETFs tokenizados** (Ondo, xStock, Robinhood) — filtro por nome checado contra o top 1000 real.

**Sinais de listagem** (subnota, 15% do peso, só neste modo):

| Sinal | Fonte | Pontos |
|---|---|---|
| **Binance Alpha Spotlight** — a vitrine de pré-listagem da Binance | categoria no CoinGecko | +30 |
| **Perpétuo na Binance Futures sem spot** — a Binance já negocia o derivativo | lista de perpétuos da Binance | +25 |
| **Portfólio YZi Labs** (ex-Binance Labs) | categoria no CoinGecko | +15 |
| **Programas Binance** (HODLer Airdrops, Launchpool, Launchpad, Megadrop, Wallet IDO, Buildkey) | categorias no CoinGecko | +10 |
| **Corretoras tier-1** (Coinbase, OKX, Bybit, Upbit, Kraken, Bithumb) | mercados do CoinGecko | +15 (≥ 3) · +8 (≥ 1) |
| Só negocia em DEX | mercados do CoinGecko | −15 |

Base 30. Um ativo que já tem mercado na Binance (outro ticker) é **vetado** aqui.

**Contrato** (GoPlus, grátis, EVM e Solana): sinais graves vetam (`contract_risk`); os moderados (emissão liberada, pausa de transferências, blacklist, proxy, código fechado, top 10 carteiras com > 50%) viram risco e aparecem na mensagem. Moedas nativas (sem contrato) não passam por essa checagem.

**"Com fundamento" é regra para chegar à IA:** fundamentos no DefiLlama **ou** um sinal oficial da Binance num projeto com descrição oficial e fora da categoria Meme do CoinGecko. Memecoin só passa com protocolo e dados por trás. A regra é aplicada **antes** do estágio profundo, para as vagas irem só para quem pode passar. Token só-DEX sem contrato checado não vai. Abaixo de US$ 30M: nota mínima +5 e cobertura ≥ 70%.

**Cotas:** até 15 ativos com histórico, 8 com dados profundos e 3 na IA por varredura (`PRE_LISTING_MAX_AI_CANDIDATES`), separados das cotas do modo Binance.

**A IA** recebe o modo e é proibida de afirmar ou prometer listagem; pesa liquidez, onde negocia e o contrato; exige evidência claramente mais forte para só-DEX e small caps.

**Placar:** preços do CoinGecko a cada 6h. Quando o ativo aparece no spot da Binance, o bot avisa e o `/status` conta **quantos sinais Pré-Binance foram listados depois do alerta** — a medida honesta de se a tese funciona.

<div align="right"><a href="#topo">▲ voltar ao topo</a> · <a href="03-operacao-e-deploy.md">Próximo: Operação e deploy →</a></div>
