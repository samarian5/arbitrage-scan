-- Migration: optimize dashboard query performance
-- Fixes: statement timeout (57014) on ar_opportunities view

-- 1. Index for fast latest-snapshot lookup
create index if not exists idx_snapshots_listing_captured 
  on public.ar_snapshots (listing_id, captured_at desc);

-- 2. Increase statement timeout for anon/authenticated roles
alter role anon set statement_timeout = '10s';
alter role authenticated set statement_timeout = '10s';

-- 3. Grant select on ar_opportunities (already granted, but ensure)
grant select on public.ar_opportunities to anon, authenticated;
