-- Apply once after 202610220001_transformx.sql. Existing jobs/files are preserved.
begin;
alter table public.tx_workers add column if not exists capabilities jsonb not null default '{}';
alter table public.tx_jobs add column if not exists assigned_worker_id uuid references public.tx_workers(id);
create index if not exists tx_jobs_assigned_queue on public.tx_jobs(assigned_worker_id,created) where status='queued';
create or replace function public.tx_claim(p_worker uuid) returns setof public.tx_jobs
language plpgsql security definer set search_path='' as $$
declare picked uuid;
begin
 perform 1 from public.tx_workers w join public.tx_profiles p on p.id=w.owner
 where w.id=p_worker and w.enabled and p.active and p.role in ('customer','mentor','admin')
 and w.heartbeat>now()-interval '60 seconds' and w.capabilities->>'ready'='true'
 for update of w;
 if not found then return; end if;
 if exists(select 1 from public.tx_jobs where worker_id=p_worker and status='running') then return; end if;
 select j.id into picked from public.tx_jobs j join public.tx_workers w on w.id=p_worker
 where j.status='queued' and j.assigned_worker_id=w.id and j.owner=w.owner
 and (w.capabilities->'operations') ? (j.options->>'operation')
 order by j.created limit 1 for update of j skip locked;
 if picked is null then return; end if;
 return query update public.tx_jobs set status='running',stage='starting',worker_id=p_worker,
 lease=gen_random_uuid(),heartbeat=now(),updated=now() where id=picked returning *;
end; $$;
revoke all on function public.tx_claim(uuid) from public,anon,authenticated;
grant execute on function public.tx_claim(uuid) to service_role;
create or replace function public.tx_revoke_worker(p_worker uuid,p_owner uuid) returns boolean
language plpgsql security definer set search_path='' as $$
begin
 perform 1 from public.tx_workers where id=p_worker and owner=p_owner for update;
 if not found then return false; end if;
 if exists(select 1 from public.tx_jobs where worker_id=p_worker and status='running') then return false; end if;
 update public.tx_workers set enabled=false where id=p_worker and owner=p_owner;
 return true;
end; $$;
revoke all on function public.tx_revoke_worker(uuid,uuid) from public,anon,authenticated;
grant execute on function public.tx_revoke_worker(uuid,uuid) to service_role;
commit;
