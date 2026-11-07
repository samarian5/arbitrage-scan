-- Migration 0006: scale-proof read path
--
-- Problem: the dashboard read model recomputed "latest snapshot per listing"
-- with DISTINCT ON over the whole ar_snapshots table on every request. At
-- 230k+ snapshots (and growing ~1.3k/min) that exceeded the statement timeout
-- and returned 500 / code 57014.
--
-- Fix: maintain a small per-listing "latest price" table incrementally from
-- ar_ingest, and read the opportunities view off that instead of history.

-- ---------------------------------------------------------------- 1. ar_latest
create table if not exists public.ar_latest (
  listing_id     bigint primary key references public.ar_listings(id) on delete cascade,
  price_cents    integer,
  shipping_cents integer not null default 0,
  captured_at    timestamptz not null default now(),
  units_sold     integer
);

-- backfill once from history (no-op on re-run)
insert into public.ar_latest (listing_id, price_cents, shipping_cents, captured_at, units_sold)
select distinct on (listing_id)
       listing_id, price_cents, coalesce(shipping_cents, 0), captured_at, units_sold
  from public.ar_snapshots
 where price_cents is not null
 order by listing_id, captured_at desc
on conflict (listing_id) do nothing;

create index if not exists idx_ar_latest_captured on public.ar_latest (captured_at desc);
create index if not exists idx_ar_latest_units    on public.ar_latest (units_sold desc nulls last);

-- ------------------------------------------------------- 2. ingest keeps it hot
create or replace function public.ar_ingest(p_run_id text, p_payload jsonb)
returns jsonb language plpgsql security definer as $$
declare v_listing_id bigint; v_rows int := 0; v_price int; it jsonb;
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

    v_price := (it->>'price_cents')::int;

    insert into public.ar_snapshots (listing_id, run_id, price_cents, shipping_cents,
                                     currency, seller, condition, in_stock, units_sold)
    values (v_listing_id, p_run_id, v_price, coalesce((it->>'shipping_cents')::int,0),
            coalesce(it->>'currency','USD'), it->>'seller', it->>'condition',
            coalesce((it->>'in_stock')::boolean, true),
            (it->>'units_sold')::int)
    on conflict (listing_id, run_id) do nothing;

    -- keep the read model current without touching full history
    if v_price is not null then
      insert into public.ar_latest (listing_id, price_cents, shipping_cents, captured_at, units_sold)
      values (v_listing_id, v_price, coalesce((it->>'shipping_cents')::int,0), now(),
              (it->>'units_sold')::int)
      on conflict (listing_id) do update
        set price_cents    = excluded.price_cents,
            shipping_cents = excluded.shipping_cents,
            captured_at    = excluded.captured_at,
            units_sold     = coalesce(excluded.units_sold, ar_latest.units_sold)
        where excluded.captured_at >= ar_latest.captured_at;
    end if;

    insert into public.ar_occurrences (run_id, keyword_id, platform, listing_id, page, position)
    values (p_run_id, (it->>'keyword_id')::bigint, it->>'platform', v_listing_id,
            (it->>'page')::int, (it->>'position')::int)
    on conflict (run_id, platform, listing_id) do nothing;

    v_rows := v_rows + 1;
  end loop;
  return jsonb_build_object('rows', v_rows);
end $$;

-- --------------------------------------------------- 3. rebuild read model
drop view if exists public.ar_queue_stats;
drop view if exists public.ar_opportunities;

create view public.ar_opportunities as
select
  m.id                              as match_id,
  m.method, m.score, m.evidence, m.keyword_id,
  e.platform_id                     as ebay_item_id,
  e.title                           as ebay_title,
  e.url                             as ebay_url,
  e.image_url                       as ebay_image,
  le.price_cents                    as ebay_price,
  le.units_sold                     as ebay_units_sold,
  a.platform_id                     as asin,
  a.title                           as amazon_title,
  a.url                             as amazon_url,
  la.price_cents                    as amazon_price,
  (le.price_cents - la.price_cents) as gross_gap,
  round(le.price_cents * (select (value)::numeric/100 from public.ar_settings where key='ebay_fee_pct'))::int
    + (select (value)::int from public.ar_settings where key='ebay_fee_fixed_cents') as ebay_fee,
  (le.price_cents - la.price_cents)
    - (round(le.price_cents * (select (value)::numeric/100 from public.ar_settings where key='ebay_fee_pct'))::int
       + (select (value)::int from public.ar_settings where key='ebay_fee_fixed_cents')) as net_gap,
  round(100.0 * (le.price_cents - la.price_cents) / nullif(la.price_cents,0), 1) as gap_pct,
  round(100.0 * ((le.price_cents - la.price_cents)
      - (round(le.price_cents * (select (value)::numeric/100 from public.ar_settings where key='ebay_fee_pct'))::int
         + (select (value)::int from public.ar_settings where key='ebay_fee_fixed_cents')))
      / nullif(la.price_cents,0), 1) as roi_pct,
  greatest(le.captured_at, la.captured_at) as as_of
from public.ar_matches m
join public.ar_listings e on e.id = m.ebay_id
join public.ar_listings a on a.id = m.amazon_id
join public.ar_latest  le on le.listing_id = e.id
join public.ar_latest  la on la.listing_id = a.id;

grant select on public.ar_opportunities to anon, authenticated;

-- one pass per table, one pass for all three gap thresholds
create view public.ar_queue_stats as
select
  k.keywords, k.due_now, k.running, k.blocked,
  l.listings, l.ebay_listings, l.amazon_listings,
  m.matches, m.snapshots,
  o.opps_5, o.opps_25, o.opps_100,
  r.last_run, r.runs_24h, r.pages_today,
  b.page_budget
from
  (select count(*) as keywords,
          count(*) filter (where enabled and due_at <= now()) as due_now,
          count(*) filter (where status = 'running') as running,
          count(*) filter (where status = 'blocked') as blocked
     from public.ar_keywords) k
  cross join
  (select count(*) as listings,
          count(*) filter (where platform = 'ebay')   as ebay_listings,
          count(*) filter (where platform = 'amazon') as amazon_listings
     from public.ar_listings) l
  cross join
  (select (select count(*) from public.ar_matches)   as matches,
          (select reltuples::bigint from pg_class where relname = 'ar_snapshots') as snapshots) m
  cross join
  (select count(*) filter (where net_gap >= 500)   as opps_5,
          count(*) filter (where net_gap >= 2500)  as opps_25,
          count(*) filter (where net_gap >= 10000) as opps_100
     from public.ar_opportunities) o
  cross join
  (select max(started_at) as last_run,
          count(*) filter (where started_at > now() - interval '24 hours') as runs_24h,
          coalesce(sum(page_to - page_from + 1)
                   filter (where started_at >= date_trunc('day', now() at time zone 'utc')), 0) as pages_today
     from public.ar_runs) r
  cross join
  (select coalesce((value)::int, 6000) as page_budget
     from public.ar_settings where key = 'daily_page_budget') b;

grant select on public.ar_queue_stats to anon, authenticated;

-- ------------------------------------------------------------- 4. join support
create index if not exists idx_matches_amazon on public.ar_matches (amazon_id);
create index if not exists idx_matches_net    on public.ar_matches (id desc);
create index if not exists idx_listings_platform_seen on public.ar_listings (platform, last_seen desc);
