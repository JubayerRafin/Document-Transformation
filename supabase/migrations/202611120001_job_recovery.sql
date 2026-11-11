-- Apply once after 202610220002_private_workers.sql. Existing jobs/files are preserved.
-- Recovers jobs whose worker stopped reporting (pod stopped, crash, network loss).
-- A recovered job becomes 'interrupted'; it is never replayed automatically.
-- A late result for an interrupted job is rejected by tx_finish's lease check.
begin;

create or replace function public.tx_interrupt_stale(p_owner uuid,p_worker uuid,p_grace_seconds integer)
returns integer language plpgsql security definer set search_path='' as $$
declare changed integer;
begin
 -- Running jobs heartbeat every ~10 s; a short grace would interrupt live work.
 if p_grace_seconds is null or p_grace_seconds<60 then raise exception 'GRACE_TOO_SHORT'; end if;
 with stale as (
  update public.tx_jobs set status='interrupted',stage='interrupted',error_code='WORKER_HEARTBEAT_LOST',updated=now()
  where status='running'
   and (p_owner is null or owner=p_owner)
   and (p_worker is null or worker_id=p_worker)
   and coalesce(heartbeat,updated)<now()-make_interval(secs=>p_grace_seconds)
  returning id)
 insert into public.tx_events(job_id,stage) select id,'interrupted' from stale;
 get diagnostics changed=row_count;
 return changed;
end; $$;

-- Same as 202610220002 plus one step: a worker that is claiming is idle (the
-- agent runs one job at a time), so its own silent running job has ended
-- without a result. Interrupt it instead of blocking the worker forever.
create or replace function public.tx_claim(p_worker uuid) returns setof public.tx_jobs
language plpgsql security definer set search_path='' as $$
declare picked uuid;
begin
 perform 1 from public.tx_workers w join public.tx_profiles p on p.id=w.owner
 where w.id=p_worker and w.enabled and p.active and p.role in ('customer','mentor','admin')
 and w.heartbeat>now()-interval '60 seconds' and w.capabilities->>'ready'='true'
 for update of w;
 if not found then return; end if;
 perform public.tx_interrupt_stale(null,p_worker,120);
 if exists(select 1 from public.tx_jobs where worker_id=p_worker and status='running') then return; end if;
 select j.id into picked from public.tx_jobs j join public.tx_workers w on w.id=p_worker
 where j.status='queued' and j.assigned_worker_id=w.id and j.owner=w.owner
 and (w.capabilities->'operations') ? (j.options->>'operation')
 order by j.created limit 1 for update of j skip locked;
 if picked is null then return; end if;
 return query update public.tx_jobs set status='running',stage='starting',worker_id=p_worker,
 lease=gen_random_uuid(),heartbeat=now(),updated=now() where id=picked returning *;
end; $$;

-- Same as 202610220002, but a worker whose job went silent can be revoked.
create or replace function public.tx_revoke_worker(p_worker uuid,p_owner uuid) returns boolean
language plpgsql security definer set search_path='' as $$
begin
 perform 1 from public.tx_workers where id=p_worker and owner=p_owner for update;
 if not found then return false; end if;
 perform public.tx_interrupt_stale(p_owner,p_worker,900);
 if exists(select 1 from public.tx_jobs where worker_id=p_worker and status='running') then return false; end if;
 update public.tx_workers set enabled=false where id=p_worker and owner=p_owner;
 return true;
end; $$;

revoke all on function public.tx_interrupt_stale(uuid,uuid,integer) from public,anon,authenticated;
grant execute on function public.tx_interrupt_stale(uuid,uuid,integer) to service_role;
revoke all on function public.tx_claim(uuid),public.tx_revoke_worker(uuid,uuid) from public,anon,authenticated;
grant execute on function public.tx_claim(uuid),public.tx_revoke_worker(uuid,uuid) to service_role;
commit;
