<a id="topo"></a>
<div align="right"><a href="../README.md">← voltar ao README</a></div>

# 🛰️ 03 — Operação e deploy

Como é o dia a dia com o radar ligado, como ler o placar, quanto ele custa e o que considerar para deixá-lo rodando 24/7.

## Índice

1. [Anatomia do alerta](#1-anatomia-do-alerta)
2. [O /analyze](#2-o-analyze)
3. [Placar dos sinais](#3-placar-dos-sinais)
4. [Lendo o /status](#4-lendo-o-status)
5. [Watchlist](#5-watchlist)
6. [Quando o bot fala sozinho](#6-quando-o-bot-fala-sozinho)
7. [Custos](#7-custos)
8. [Rodando 24/7](#8-rodando-247)

---

## 1. Anatomia do alerta

```text
🟢 OPORTUNIDADE · 🚀 PRÉ-BINANCE          ← veredito · modo (🟢 BINANCE = já listada)
$FLUID — Fluid                             ← ativo
Lending e DEX na mesma liquidez; …         ← o que o projeto faz (≤ 110 caracteres)

✅ Binance Alpha · Perp na Binance · …     ← até 3 a favor, escolhidos pelo código
⚠️ Top 10 com 53%                          ← até 3 contra (some se não houver)

Entrada $3.90–$4.05 · Upbit, Bybit, DEX    ← zona de entrada · onde comprar (tier-1 primeiro)
🎯 $5.20 (+30.0%) · 🛑 $3.40 (-15.0%)       ← alvo e invalidação (stop da tese)
Risco Médio · Confiança 72% · Limite R$ 600,00

Detalhes: /detalhe FLUID                   ← relatório completo, sem nova chamada à IA
```

**A favor** pode trazer: preço caindo com receita subindo, Binance Alpha, perpétuo na Binance, YZi Labs, receita/fees/TVL crescendo, receita repassada a holders, recuo dentro de tendência de alta, cruz de ouro, alavancagem limpa. **Contra**: mercado em risco, tendência de baixa, esticado/parabólico, diluição alta, comprados demais, concentração nas 10 maiores carteiras, sem receita/TVL, só DEX, memecoin, contrato mintável.

O **`/detalhe`** traz a nota completa em seções — tese e utilidade, sinais de listagem e contrato, valuation (topo histórico, par BTC, FDV, diluição, receita, TVL), técnico (tendência, MA50×MA200, RSI, alavancagem), contexto macro (regime, Fear & Greed), por que agora e o plano — mais nota, cobertura de dados e principal risco.

## 2. O /analyze

Um pouco mais de contexto, ainda curto. Quatro respostas possíveis:

| Cabeçalho | Significado |
|---|---|
| 🟢 **ALERTA** | passaria no radar agora |
| 👀 **OBSERVAR** | interessante, mas ainda não (confiança ou nota abaixo da régua, ou a IA pediu observação) |
| ⚪ **NÃO PASSOU** | a IA rejeitou ou os níveis eram incoerentes |
| 🔴 **BLOQUEADO** | veto — a resposta lista os motivos e a IA nem é chamada |

Responde no mesmo formato curto do alerta; `/detalhe` mostra o relatório completo com a nota final (0–100), a cobertura de dados e a confiança já ajustada. Ativo vetado não chama a IA: sai sem tese nem plano, com os motivos do veto. Funciona para qualquer ativo do top 200, mesmo que não passasse nos filtros do radar.

## 3. Placar dos sinais

Cada alerta vira um **sinal** salvo com preço no alerta, entrada, alvo, invalidação, confiança, subnotas e o dossiê completo que a IA leu.

A cada `TRACKER_INTERVAL_MINUTES` (30), o radar lê as velas de 4h da Binance desde o alerta — ou, para sinais 🚀 Pré-Binance, os preços horários do CoinGecko, conferidos a cada 6h por causa da cota:

| Estado | Quando |
|---|---|
| `OPEN` | nenhum nível tocado ainda |
| `TARGET_HIT` | a máxima de uma vela tocou o alvo |
| `INVALIDATED` | a mínima de uma vela tocou a invalidação |
| `EXPIRED` | passou `OPPORTUNITY_EXPIRY_DAYS` (90) sem tocar nenhum |

Regras que mantêm o placar honesto:

- **Vale o primeiro nível tocado**, em ordem cronológica.
- **Sinal Pré-Binance que aparece no spot da Binance** gera um aviso próprio (`🚀 $X foi listada no spot da Binance!`) e entra na contagem do `/status`.
- **Vela que toca os dois níveis conta como invalidada** — dentro de 1h não dá para saber a ordem, e o placar nunca é inflado.
- **Expirado não é vitória nem derrota**: aparece separado e fica fora do win rate.
- O resultado é medido a partir do **preço no alerta**.

Cada mudança de estado é avisada no chat:

```text
🎯 LDO bateu o alvo
$1.2 → $1.42 (+18.3%)
```

## 4. Lendo o /status

```text
📊 RADAR

Sinais: 24
Alvo atingido: 13
Invalidado: 6
Expirado: 0
Em aberto: 5

Win rate encerrados: 68%

Últimos 30d:
9 sinais · 6 alvo · 2 invalidados · 1 abertos

🚀 Pré-Binance: 7 sinais · 2 listados na Binance depois do alerta

🩺 Saúde
Mercado: 🟢 favorável — BTC -2.0% em 7d e +7.8% em 30d, acima da média de 50 dias.
Última varredura: 28/09 10:57 (há 12 min) → 5 na IA · 1 alerta(s) · 2 observação · 0 erros
Próxima: 28/09 11:57
Fontes: DefiLlama ✅ · Derivativos ✅ (Binance) · Unlocks ⚪ · Social ⚪ · Notícias ⚪ · Hacks ✅ · On-chain ⚪
Supabase: ligado
```

| Linha | Como ler |
|---|---|
| **Win rate** | alvo ÷ (alvo + invalidado). Com poucos sinais encerrados, é ruído — espere algumas dezenas |
| **N na IA** | zero várias vezes seguidas = mercado sem candidatos ou régua alta (ex.: `risk_off`) |
| **observação** | a IA ou as travas seguraram — use `/analyze` para ver por quê |
| **erros** | a IA falhou (chave, crédito, timeout) — rode `python smoke_test.py --ai` |
| **Fontes ⚪** | sem chave ou camada desligada — esperado se você não usa aquele provedor |

Horários em Brasília.

## 5. Watchlist

`/watch PENDLE` garante que o ativo passe pela análise completa (histórico, dados profundos) em toda varredura, mesmo fora do corte dos melhores. Ele ainda precisa passar nos filtros e nos vetos para virar alerta. `/watch` sem nada lista; `/unwatch PENDLE` tira. Com Supabase, a lista sobrevive a reinícios.

## 6. Quando o bot fala sozinho

| Mensagem | Quando |
|---|---|
| 🟢 **Crypto Radar iniciado** | toda vez que o processo sobe — se aparecer sem você reiniciar, o servidor reiniciou o bot |
| 🟢 **OPORTUNIDADE** | um ativo passou em todas as camadas |
| 🎯 / 🛑 / ⌛ | um sinal bateu o alvo, foi invalidado ou expirou |
| 🚀 **foi listada no spot da Binance** | um sinal Pré-Binance apareceu na Binance |
| ⚠️ **Crypto Radar com problema** | varredura falhou ou a IA errou — no máximo uma vez a cada 3 horas |

Silêncio por horas é normal: significa que nada convergiu. Para ter certeza de que está vivo, `/status`.

## 7. Custos

| Item | Consumo aproximado (varredura a cada 60 min) | Custo |
|---|---|---|
| **OpenAI** | até `MAX_AI_CANDIDATES` + `PRE_LISTING_MAX_AI_CANDIDATES` análises por varredura + 1 por `/analyze` | **pago** — o principal custo |
| CoinGecko | ~7 mil chamadas/mês com os dois modos (4 páginas de mercado por varredura, históricos, perfis, placar Pré-Binance) | grátis no plano Demo (10 mil/mês) |
| GoPlus | 1 chamada por finalista Pré-Binance a cada 24h | grátis |
| DefiLlama, Binance Spot/Futures, Telegram | dados públicos, em cache | grátis |
| Supabase | poucas linhas por dia | grátis |
| Tokenomist | ~1 chamada por finalista a cada 12h | plano Pro ou acima |
| CoinGlass | 4 chamadas por finalista a cada 5 min de cache | Hobbyist ou acima |
| LunarCrush | 1 lista + 1 série por finalista a cada 15 min de cache | Individual ou acima |
| NewsData | 1 chamada para até 5 finalistas a cada 2h | grátis (200 créditos/dia) |

Para cortar custo da IA, na ordem de impacto:

1. **Aumentar `SCAN_INTERVAL_MINUTES`** — de 60 para 120 corta pela metade.
2. **Baixar `MAX_AI_CANDIDATES`** — de 5 para 3.
3. **Subir `MIN_SCORE_TO_AI`** — menos ativos chegam à IA.

## 8. Rodando 24/7

O radar só varre e acompanha sinais enquanto o processo `python main.py` estiver vivo. No computador pessoal, ele para quando o terminal fecha ou a máquina dorme.

**O que o servidor precisa ter:**

- Python 3.10+ e acesso de saída à internet — o bot usa *polling*, então **não precisa de porta aberta, domínio nem HTTPS**.
- O `.env` configurado direto no servidor (ou as variáveis no painel do provedor). Nunca no repositório.
- Reinício automático se o processo cair.
- **Supabase configurado** — sem ele, placar e watchlist zeram a cada reinício do servidor.

**Opções que funcionam para esse perfil de app** (processo contínuo, sem site):

| Tipo | Exemplos | Observação |
|---|---|---|
| VPS | Hetzner, DigitalOcean, Contabo, Oracle Cloud | Mais controle; reinício automático via `systemd` |
| Plataforma com *worker* | Railway, Render (Background Worker), Fly.io | Mais simples; configurar como processo contínuo, não como site |

> [!IMPORTANT]
> **Duas regras que derrubam o bot se ignoradas:**
> 1. **Região fora dos EUA.** A Binance bloqueia acesso a partir dos EUA (HTTP 451), no spot e nos futuros. Prefira São Paulo ou Europa.
> 2. **Uma instância por token.** Se o bot estiver rodando no servidor e no seu computador ao mesmo tempo, o Telegram derruba um deles com erro `Conflict`.

<div align="right"><a href="#topo">▲ voltar ao topo</a> · <a href="../README.md">Voltar ao README →</a></div>
