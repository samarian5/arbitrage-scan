-- Arbitrage scanner schema. Prefix `ar_` so it coexists with the eb_* keyword
-- research tables in the same Supabase project.
--
-- Shape: Listing (stable identity) × Snapshot (an observation) × Occurrence
-- (where it ranked) — plus Match (cross-platform identity) and a read model for
-- the dashboard. Nothing here stores a "current price" column: the latest
-- snapshot is the price, so history is never overwritten.

create table if not exists public.ar_keywords (
  id            bigserial primary key,
  keyword       text unique not null,
  enabled       boolean not null default true,
  depth         int  not null default 2,          -- pages per pass
  next_page     int  not null default 1,
  pass_no       int  not null default 1,
  interval_hours int not null default 24,
  due_at        timestamptz not null default now(),
  last_run_at   timestamptz,
  priority      int  not null default 0,
  status        text not null default 'queued',   -- queued|running|blocked|paused
  fails         int  not null default 0,
  created_at    timestamptz not null default now()
);
create index if not exists ar_keywords_due_idx on public.ar_keywords (due_at) where enabled;

create table if not exists public.ar_listings (
  id          bigserial primary key,
  platform    text not null check (platform in ('ebay','amazon')),
  platform_id text not null,
  url         text,
  title       text,
  brand       text,
  gtin        text,
  mpn         text,
  image_url   text,
  norm        text,                                -- normalised title, computed at parse time
  tokens      text[],                              -- content tokens for matching
  first_seen  timestamptz not null default now(),
  last_seen   timestamptz not null default now(),
  unique (platform, platform_id)
);
create index if not exists ar_listings_tokens_idx on public.ar_listings using gin (tokens);

create table if not exists public.ar_snapshots (
  id            bigserial primary key,
  listing_id    bigint not null references public.ar_listings(id) on delete cascade,
  run_id        text not null,
  price_cents   int,
  shipping_cents int default 0,
  currency      text default 'USD',
  seller        text,
  condition     text,
  in_stock      boolean default true,
  captured_at   timestamptz not null default now(),
  unique (listing_id, run_id)                     -- one observation per listing per run
);
create index if not exists ar_snapshots_latest_idx on public.ar_snapshots (listing_id, captured_at desc);

create table if not exists public.ar_occurrences (
  id          bigserial primary key,
  run_id      text not null,
  keyword_id  bigint references public.ar_keywords(id) on delete set null,
  platform    text not null,
  listing_id  bigint not null references public.ar_listings(id) on delete cascade,
  page        int,
  position    int,
  unique (run_id, platform, listing_id)
);

create table if not exists public.ar_runs (
  id           bigserial primary key,
  run_id       text unique not null,
  keyword_id   bigint,
  keyword      text,
  shard        text,
  status       text not null,                     -- running|succeeded|failed|aborted
  stopped_reason text,
  page_from    int,
  page_to      int,
  ebay_seen    int default 0,
  amazon_seen  int default 0,
  rows_written int default 0,
  matches      int default 0,
  challenges   int default 0,
  tiers        text,
  duration_s   numeric,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz
);
create index if not exists ar_runs_started_idx on public.ar_runs (started_at desc);

create table if not exists public.ar_matches (
  id            bigserial primary key,
  ebay_id       bigint not null references public.ar_listings(id) on delete cascade,
  amazon_id     bigint not null references public.ar_listings(id) on delete cascade,
  keyword_id    bigint,
  method        text not null,                    -- model|title
  score         numeric not null,
  evidence      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  unique (ebay_id, amazon_id)
);
create index if not exists ar_matches_ebay_idx on public.ar_matches (ebay_id);

create table if not exists public.ar_settings (
  key   text primary key,
  value text
);
insert into public.ar_settings (key, value) values
  ('ebay_fee_pct', '13.25'),
  ('ebay_fee_fixed_cents', '30'),
  ('min_gap_cents', '500'),
  ('min_roi_pct', '15'),
  ('daily_page_budget', '6000'),
  ('match_threshold', '0.72')
on conflict (key) do nothing;

-- ─────────────────────────── queue: claim / finish ───────────────────────────
-- Round-robin by due_at, per-keyword cadence, daily page budget. SKIP LOCKED is
-- what lets N runners (in N repos) pull at once without ever colliding.
create or replace function public.ar_claim_slice(p_slot text default null, p_pages int default null)
returns table (keyword_id bigint, keyword text, page_from int, page_to int,
               run_id text, depth int, pass_no int, budget_left int)
language plpgsql security definer as $$
#variable_conflict use_column
declare
  k public.ar_keywords%rowtype;
  v_pages int;
  v_budget int;
  v_used int;
begin
  -- Reap slices abandoned by a runner that died mid-flight (workflow cancel,
  -- OOM, 70-minute timeout). Without this they stay 'running' forever and their
  -- keyword is never claimed again.
  update public.ar_keywords
     set status = 'queued', due_at = now()
   where status = 'running'
     and coalesce(last_run_at, now() - interval '2 hours') < now() - interval '45 minutes';
  select coalesce((value)::int, 6000) into v_budget from public.ar_settings where key='daily_page_budget';
  select coalesce(sum(page_to - page_from + 1), 0) into v_used
    from public.ar_runs
   where started_at >= date_trunc('day', now() at time zone 'utc')
     and page_from is not null and page_to is not null;

  if v_used >= v_budget then
    return;
  end if;

  select * into k from public.ar_keywords
   where enabled and status in ('queued','running') and due_at <= now()
   order by due_at asc, priority desc, id asc
   limit 1
   for update skip locked;

  if not found then
    return;
  end if;

  v_pages := least(coalesce(p_pages, k.depth), greatest(v_budget - v_used, 1));
  -- clamp the slice to the remaining depth of this pass
  v_pages := least(v_pages, k.depth - k.next_page + 1);

  update public.ar_keywords
     set status = 'running',
         last_run_at = now(),
         due_at = now() + make_interval(hours => interval_hours)
   where id = k.id;

  return query select k.id, k.keyword, k.next_page, k.next_page + v_pages - 1,
                      'run-' || to_char(now() at time zone 'utc','YYYYMMDD"T"HH24MISS')
                        || '-' || substr(md5(random()::text), 1, 6),
                      k.depth, k.pass_no, v_budget - v_used;
end $$;

-- Return the slice to the queue and advance the pass when depth is exhausted.
create or replace function public.ar_finish_slice(
  p_keyword_id bigint, p_pages_done int, p_status text default 'queued',
  p_reason text default null)
returns void language plpgsql security definer as $$
declare k public.ar_keywords%rowtype;
begin
  select * into k from public.ar_keywords where id = p_keyword_id;
  if not found then return; end if;

  if p_status = 'blocked' then
    -- a block is usually transient (rate limit, bad minute, cold profile), so
    -- back off instead of parking the keyword: 20m, 40m, 80m ... capped at 6h.
    update public.ar_keywords
       set status = 'queued',
           fails = fails + 1,
           due_at = now() + least(interval '6 hours',
                                  interval '20 minutes' * power(2, least(fails, 4))),
           last_reason = left(coalesce(p_reason,'blocked'), 200)
     where id = k.id;
    return;
  end if;

  if k.next_page + coalesce(p_pages_done,0) > k.depth then
    update public.ar_keywords
       set next_page = 1, pass_no = pass_no + 1, status = coalesce(p_status,'queued'),
           fails = case when p_status='queued' then 0 else fails end
     where id = k.id;
  else
    update public.ar_keywords
       set next_page = next_page + coalesce(p_pages_done,0),
           status = coalesce(p_status,'queued'),
           fails = case when p_status='queued' then 0 else fails end
     where id = k.id;
  end if;
end $$;

-- ─────────────────────────── ingest (idempotent) ────────────────────────────
create or replace function public.ar_ingest(p_run_id text, p_payload jsonb)
returns jsonb language plpgsql security definer as $$
declare
  it jsonb;
  v_listing_id bigint;
  v_rows int := 0;
begin
  for it in select * from jsonb_array_elements(p_payload) loop
    insert into public.ar_listings (platform, platform_id, url, title, brand, gtin, mpn,
                                    image_url, norm, tokens, last_seen)
    values (it->>'platform', it->>'platform_id', it->>'url', it->>'title', it->>'brand',
            it->>'gtin', it->>'mpn', it->>'image_url', it->>'norm',
            coalesce(array(select jsonb_array_elements_text(it->'tokens')), '{}'::text[]),
            now())
    on conflict (platform, platform_id) do update
      set title = coalesce(excluded.title, ar_listings.title),
          brand = coalesce(excluded.brand, ar_listings.brand),
          gtin  = coalesce(excluded.gtin,  ar_listings.gtin),
          mpn   = coalesce(excluded.mpn,   ar_listings.mpn),
          image_url = coalesce(excluded.image_url, ar_listings.image_url),
          norm  = coalesce(excluded.norm,  ar_listings.norm),
          tokens = case when cardinality(excluded.tokens) > 0 then excluded.tokens else ar_listings.tokens end,
          last_seen = now()
    returning id into v_listing_id;

    insert into public.ar_snapshots (listing_id, run_id, price_cents, shipping_cents,
                                     currency, seller, condition, in_stock)
    values (v_listing_id, p_run_id, (it->>'price_cents')::int, coalesce((it->>'shipping_cents')::int,0),
            coalesce(it->>'currency','USD'), it->>'seller', it->>'condition',
            coalesce((it->>'in_stock')::boolean, true))
    on conflict (listing_id, run_id) do nothing;

    insert into public.ar_occurrences (run_id, keyword_id, platform, listing_id, page, position)
    values (p_run_id, (it->>'keyword_id')::bigint, it->>'platform', v_listing_id,
            (it->>'page')::int, (it->>'position')::int)
    on conflict (run_id, platform, listing_id) do nothing;

    v_rows := v_rows + 1;
  end loop;
  return jsonb_build_object('rows', v_rows);
end $$;

create or replace function public.ar_upsert_matches(p_matches jsonb)
returns int language plpgsql security definer as $$
declare n int := 0; it jsonb;
begin
  for it in select * from jsonb_array_elements(p_matches) loop
    insert into public.ar_matches (ebay_id, amazon_id, keyword_id, method, score, evidence)
    values ((it->>'ebay_id')::bigint, (it->>'amazon_id')::bigint, (it->>'keyword_id')::bigint,
            it->>'method', (it->>'score')::numeric, coalesce(it->'evidence','{}'::jsonb))
    on conflict (ebay_id, amazon_id) do update
      set score = excluded.score, method = excluded.method, evidence = excluded.evidence;
    n := n + 1;
  end loop;
  return n;
end $$;

-- ─────────────────────────── dashboard read model ───────────────────────────
create or replace view public.ar_opportunities as
with latest as (
  select distinct on (listing_id) listing_id, price_cents, shipping_cents, captured_at
    from public.ar_snapshots
   where price_cents is not null
   order by listing_id, captured_at desc
),
fees as (
  select (value)::numeric/100 as fvf from public.ar_settings where key='ebay_fee_pct'
),
fixed as (
  select (value)::int as c from public.ar_settings where key='ebay_fee_fixed_cents'
)
select
  m.id                              as match_id,
  m.method, m.score, m.evidence, m.keyword_id,
  e.platform_id                     as ebay_item_id,
  e.title                           as ebay_title,
  e.url                             as ebay_url,
  e.image_url                       as ebay_image,
  le.price_cents                    as ebay_price,
  a.platform_id                     as asin,
  a.title                           as amazon_title,
  a.url                             as amazon_url,
  la.price_cents                    as amazon_price,
  (le.price_cents - la.price_cents) as gross_gap,
  round(le.price_cents * (select fvf from fees))::int + (select c from fixed) as ebay_fee,
  (le.price_cents - la.price_cents)
    - (round(le.price_cents * (select fvf from fees))::int + (select c from fixed)) as net_gap,
  round(100.0 * (le.price_cents - la.price_cents) / nullif(la.price_cents,0), 1) as gap_pct,
  round(100.0 * ((le.price_cents - la.price_cents)
      - (round(le.price_cents * (select fvf from fees))::int + (select c from fixed)))
      / nullif(la.price_cents,0), 1) as roi_pct,
  greatest(le.captured_at, la.captured_at) as as_of
from public.ar_matches m
join public.ar_listings e on e.id = m.ebay_id
join public.ar_listings a on a.id = m.amazon_id
join latest le on le.listing_id = e.id
join latest la on la.listing_id = a.id;

-- ─────────────────────────── RLS: public read, service write ────────────────
alter table public.ar_keywords   enable row level security;
alter table public.ar_listings   enable row level security;
alter table public.ar_snapshots  enable row level security;
alter table public.ar_occurrences enable row level security;
alter table public.ar_runs       enable row level security;
alter table public.ar_matches    enable row level security;
alter table public.ar_settings   enable row level security;

do $$
declare t text;
begin
  foreach t in array array['ar_keywords','ar_listings','ar_snapshots','ar_occurrences',
                           'ar_runs','ar_matches','ar_settings'] loop
    execute format('drop policy if exists %I_read on public.%I', t, t);
    execute format('create policy %I_read on public.%I for select to anon, authenticated using (true)', t, t);
  end loop;
end $$;

grant select on public.ar_opportunities to anon, authenticated;
grant execute on function public.ar_claim_slice(text,int) to service_role;
grant execute on function public.ar_finish_slice(bigint,int,text,text) to service_role;
grant execute on function public.ar_ingest(text,jsonb) to service_role;
grant execute on function public.ar_upsert_matches(jsonb) to service_role;
