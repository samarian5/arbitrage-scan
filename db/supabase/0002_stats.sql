-- Aggregate read model for the dashboard header. One row, no scans of big
-- tables on the client side.
create or replace view public.ar_queue_stats as
select
  (select count(*) from public.ar_keywords)                                    as keywords,
  (select count(*) from public.ar_keywords where enabled and due_at <= now())   as due_now,
  (select count(*) from public.ar_keywords where status = 'running')            as running,
  (select count(*) from public.ar_keywords where status = 'blocked')            as blocked,
  (select count(*) from public.ar_listings)                                     as listings,
  (select count(*) from public.ar_listings where platform = 'ebay')             as ebay_listings,
  (select count(*) from public.ar_listings where platform = 'amazon')           as amazon_listings,
  (select count(*) from public.ar_snapshots)                                    as snapshots,
  (select count(*) from public.ar_matches)                                      as matches,
  (select count(*) from public.ar_opportunities where net_gap >= 500)           as opps_5,
  (select count(*) from public.ar_opportunities where net_gap >= 2500)          as opps_25,
  (select count(*) from public.ar_opportunities where net_gap >= 10000)         as opps_100,
  (select max(started_at) from public.ar_runs)                                  as last_run,
  (select count(*) from public.ar_runs
    where started_at > now() - interval '24 hours')                             as runs_24h,
  (select coalesce(sum(page_to - page_from + 1), 0) from public.ar_runs
    where started_at >= date_trunc('day', now() at time zone 'utc'))            as pages_today,
  (select coalesce((value)::int, 6000) from public.ar_settings
    where key = 'daily_page_budget')                                            as page_budget;

grant select on public.ar_queue_stats to anon, authenticated;
