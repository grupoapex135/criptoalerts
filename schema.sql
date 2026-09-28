-- Crypto Radar schema. Idempotent and additive: safe to run again on an
-- existing database (it only creates what is missing; it never drops or
-- rewrites data). Run the whole file in the Supabase SQL Editor.

create extension if not exists pgcrypto;

-- ------------------------------------------------------------------ v1
create table if not exists opportunities (
    id uuid primary key default gen_random_uuid(),
    symbol text not null,
    name text,
    detected_at timestamptz not null default now(),
    price_usd numeric,
    entry_min_usd numeric,
    entry_max_usd numeric,
    target_usd numeric,
    invalidation_usd numeric,
    risk text,
    confidence integer,
    quantitative_score numeric,
    market_cap_usd numeric,
    daily_volume_usd numeric,
    venue text,
    reason text,
    ai_payload jsonb,
    raw_snapshot jsonb
);

create index if not exists opportunities_symbol_detected_idx
on opportunities(symbol, detected_at desc);

create table if not exists alerts (
    id uuid primary key default gen_random_uuid(),
    opportunity_id uuid references opportunities(id) on delete set null,
    symbol text not null,
    sent_at timestamptz not null default now(),
    telegram_chat_id text,
    message text
);

create index if not exists alerts_symbol_sent_idx
on alerts(symbol, sent_at desc);

-- ------------------------------------------------------------------ v2: multi-layer + tracking
-- Outcome tracking. Rows created before v2 start as OPEN and get tracked too.
alter table opportunities add column if not exists status text not null default 'OPEN';
alter table opportunities add column if not exists expires_at timestamptz;
alter table opportunities add column if not exists closed_at timestamptz;
alter table opportunities add column if not exists close_price numeric;
alter table opportunities add column if not exists result_pct numeric;
-- Analysis context: subscores, the full dossier the AI read, and the market regime.
alter table opportunities add column if not exists scores jsonb;
alter table opportunities add column if not exists dossier jsonb;
alter table opportunities add column if not exists market_regime text;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'opportunities_status_check') then
        alter table opportunities add constraint opportunities_status_check
            check (status in ('OPEN', 'TARGET_HIT', 'INVALIDATED', 'EXPIRED'));
    end if;
end $$;

create index if not exists opportunities_status_idx on opportunities(status);

-- ------------------------------------------------------------------ v3: Pre-Binance mode
-- binance = asset on Binance spot when alerted; pre_listing = not listed yet.
alter table opportunities add column if not exists mode text not null default 'binance';
-- CoinGecko id: pre-Binance signals are tracked on CoinGecko prices.
alter table opportunities add column if not exists coin_id text;
-- When a pre-Binance signal's asset showed up on Binance spot.
alter table opportunities add column if not exists binance_listed_at timestamptz;

create table if not exists watchlist (
    symbol text primary key,
    created_at timestamptz not null default now(),
    active boolean not null default true
);

-- RLS on with no policies: only the service role (the bot) can read/write.
-- Without this, the public anon key could read and write these tables via the REST API.
alter table opportunities enable row level security;
alter table alerts enable row level security;
alter table watchlist enable row level security;
