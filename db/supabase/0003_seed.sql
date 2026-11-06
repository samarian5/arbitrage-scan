-- Queue seeding must be timed by the database, not by the machine that loads it.
-- A host clock ~65 min ahead (seen on this workstation) pushed every due_at into
-- the future and the queue simply never fired, which reads as "crawler broken".

create or replace function public.ar_seed_keywords(
  p_keywords text[],
  p_depth int default 2,
  p_interval_hours int default 24,
  p_spread_minutes int default 1440
) returns jsonb language plpgsql security definer as $$
declare
  n int;
begin
  insert into public.ar_keywords (keyword, depth, interval_hours, due_at)
  select k, p_depth, p_interval_hours,
         now() + (((row_number() over (order by k))::int - 1) % greatest(p_spread_minutes,1))
                  * interval '1 minute'
    from unnest(p_keywords) as k
   on conflict (keyword) do nothing;

  get diagnostics n = row_count;
  -- re-space the ones that were already there but are mis-timed (future beyond
  -- one spread window) so a re-seed repairs a skewed queue instead of keeping it
  update public.ar_keywords
     set due_at = now() + ((id % greatest(p_spread_minutes,1)) * interval '1 minute')
   where due_at > now() + (p_spread_minutes * interval '1 minute');

  return jsonb_build_object('inserted', n,
                            'total', (select count(*) from public.ar_keywords),
                            'due_now', (select count(*) from public.ar_keywords
                                         where enabled and due_at <= now()));
end $$;

grant execute on function public.ar_seed_keywords(text[],int,int,int) to service_role;
