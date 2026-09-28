create extension if not exists pgcrypto;

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

-- RLS on with no policies: only the service role (the bot) can read/write.
-- Without this, the public anon key could read and write these tables via the REST API.
alter table opportunities enable row level security;
alter table alerts enable row level security;
