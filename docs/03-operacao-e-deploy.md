<a id="topo"></a>
<div align="right"><a href="../README.md">← voltar ao README</a></div>

# 🛰️ 03 — Operação e deploy

Como é o dia a dia com o radar ligado, quanto ele custa e o que considerar para deixá-lo rodando 24/7.

## Índice

1. [Anatomia do alerta](#1-anatomia-do-alerta)
2. [Lendo o /status](#2-lendo-o-status)
3. [Quando o bot fala sozinho](#3-quando-o-bot-fala-sozinho)
4. [Cooldown e histórico](#4-cooldown-e-histórico)
5. [Custos](#5-custos)
6. [Rodando 24/7](#6-rodando-247)

---

## 1. Anatomia do alerta

```text
🟢 OPORTUNIDADE — PENDLE                     ← ativo que passou em tudo

Entrada: $2.55 – $2.65                       ← zona de referência sugerida pela IA
Onde: Binance (PENDLEUSDT) · agora $2.6      ← par confirmado + preço no momento
Risco: Médio                                 ← classificação da IA
Limite configurado: R$ 600,00                ← CAPITAL_BRL × MAX_POSITION_PCT (seu teto)

🎯 Referência: $3.1 (+19.2%)                 ← alvo e distância do preço atual
🛑 Invalidação: $2.35 (-9.6%)                ← onde a tese deixa de valer
⚖️ Retorno/risco: 2,0                        ← (alvo − meio da entrada) ÷ (meio da entrada − invalidação)

Pullback de 7d com TVL crescendo...          ← justificativa da IA
⚠️ Zonas de referência, não garantias.       ← ressalva da IA
Confiança do radar: 72%                      ← sempre ≥ MIN_CONFIDENCE_TO_ALERT
```

> [!TIP]
> O **retorno/risco** é o número mais útil para decidir. Abaixo de 1,5, o alvo paga pouco pelo risco até a invalidação — mesmo com confiança alta.

## 2. Lendo o /status

```text
📡 Crypto Radar — status

Rodando desde: 28/09 09:57
Última varredura: 28/09 10:57 (há 12 min)
  → 5 na IA · 1 passaram · 0 descartados · 0 erros
Próxima automática: 28/09 11:57
Alertas enviados desde o início: 3

Modelo IA: gpt-5
Supabase: desligado (cooldown só em memória)
Filtros: mcap ≥ $100M · volume ≥ $5M · FDV/MC ≤ 3 · score ≥ 55 · confiança ≥ 65 · cooldown 24h
```

| Linha | Como ler |
|---|---|
| **N na IA** | Quantos candidatos chegaram à IA. Zero várias vezes seguidas = filtros apertados demais para o mercado atual |
| **passaram** | Viraram alerta (ou vão virar, se não estiverem em cooldown) |
| **descartados** | A IA quis alertar, mas os níveis eram incoerentes — o código barrou |
| **erros** | A IA falhou (chave, crédito, timeout). Se aparecer, rode `python smoke_test.py --ai` |
| **❌ falhou** | A varredura inteira caiu — o motivo aparece na própria linha |

Os horários são de Brasília.

## 3. Quando o bot fala sozinho

| Mensagem | Quando |
|---|---|
| 🟢 **Crypto Radar iniciado** | Toda vez que o processo sobe. Se ela aparecer sem você ter reiniciado nada, o servidor reiniciou o bot |
| 🟢 **OPORTUNIDADE** | Um ativo passou em todas as camadas |
| ⚠️ **Crypto Radar com problema** | Varredura falhou ou a IA errou. **No máximo uma vez a cada 3 horas**, para uma API fora do ar não virar spam |

Silêncio por horas é normal: significa que nada passou nos filtros. Para ter certeza de que o radar está vivo, mande `/status`.

## 4. Cooldown e histórico

Depois de alertar um ativo, o radar o ignora por `ALERT_COOLDOWN_HOURS` (24h) — e isso é conferido **antes** da IA, então não há custo com ativos repetidos.

| | Sem Supabase | Com Supabase |
|---|---|---|
| Cooldown | Em memória — **zera ao reiniciar** | Persistente |
| Histórico de alertas | Não há | Tabelas `opportunities` e `alerts` |
| Se o Supabase cair | — | O radar segue funcionando com o cooldown em memória e mostra o erro no `/status` |

O `/scan` manual mostra oportunidades mesmo que estejam em cooldown — ele é uma consulta, não um alerta.

## 5. Custos

| Item | Consumo | Custo |
|---|---|---|
| CoinGecko | 1 chamada por varredura + 1 por `/analyze` (~720/mês no padrão) | Grátis (Demo: 10 mil/mês) |
| DefiLlama, Binance, Telegram | Dados públicos | Grátis |
| Supabase | Poucas linhas por dia | Grátis |
| **OpenAI** | Até `MAX_AI_CANDIDATES` análises por varredura + 1 por `/analyze` | **Pago** — o único custo real |

Para cortar custo da IA, na ordem de impacto:

1. **Aumentar `SCAN_INTERVAL_MINUTES`** — de 60 para 120 corta pela metade.
2. **Baixar `MAX_AI_CANDIDATES`** — de 5 para 3.
3. **Subir `MIN_SCORE_TO_AI`** — menos candidatos chegam à IA.

## 6. Rodando 24/7

O radar só varre enquanto o processo `python main.py` estiver vivo. No computador pessoal, ele para quando o terminal fecha ou a máquina dorme. Para rodar sem parar, ele precisa de um servidor.

**O que o servidor precisa ter:**

- Python 3.10+ e acesso de saída à internet — o bot usa *polling*, então **não precisa de porta aberta, domínio nem HTTPS**.
- O `.env` configurado direto no servidor (ou as variáveis no painel do provedor). Nunca no repositório.
- Reinício automático se o processo cair.

**Opções que funcionam para esse perfil de app** (processo contínuo, sem site):

| Tipo | Exemplos | Observação |
|---|---|---|
| VPS | Hetzner, DigitalOcean, Contabo, Oracle Cloud | Mais controle; reinício automático via `systemd` |
| Plataforma com *worker* | Railway, Render (Background Worker), Fly.io | Mais simples; configurar como processo contínuo, não como site |

> [!IMPORTANT]
> **Duas regras que derrubam o bot se ignoradas:**
> 1. **Região fora dos EUA.** A Binance bloqueia acesso a partir dos EUA (HTTP 451). Prefira São Paulo ou Europa.
> 2. **Uma instância por token.** Se o bot estiver rodando no servidor e no seu computador ao mesmo tempo, o Telegram derruba um deles com erro `Conflict`.

<div align="right"><a href="#topo">▲ voltar ao topo</a> · <a href="../README.md">Voltar ao README →</a></div>
