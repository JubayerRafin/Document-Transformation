begin;
create table public.tx_profiles (
 id uuid primary key references auth.users(id) on delete cascade,
 role text not null default 'customer' check(role in ('customer','mentor','admin','viewer')),
 active boolean not null default true
);
create table public.tx_workers (
 id uuid primary key default gen_random_uuid(), owner uuid not null references public.tx_profiles(id),
 name text not null check(length(name) between 1 and 100), token_hash text not null,
 enabled boolean not null default true, heartbeat timestamptz, created timestamptz not null default now()
);
create table public.tx_jobs (
 id uuid primary key default gen_random_uuid(), owner uuid not null references public.tx_profiles(id),
 filename text not null, source_sha256 text not null check(source_sha256 ~ '^[a-f0-9]{64}$'),
 options jsonb not null, request_hash text not null, idempotency_key text not null,
 status text not null default 'uploading' check(status in ('uploading','queued','running','succeeded','failed','cancelled','interrupted')),
 stage text not null default 'uploading', quality text not null default 'not_reviewed',
 metrics jsonb not null default '{}', worker_id uuid references public.tx_workers(id),
 lease uuid, heartbeat timestamptz, baseline boolean not null default false,
 error_code text, created timestamptz not null default now(), updated timestamptz not null default now(),
 unique(owner,idempotency_key)
);
create unique index tx_one_job_per_worker on public.tx_jobs(worker_id) where status='running';
create index tx_jobs_owner_created on public.tx_jobs(owner,created desc);
create table public.tx_artifacts (
 id uuid primary key default gen_random_uuid(), job_id uuid not null references public.tx_jobs(id),
 kind text not null check(kind in ('source','result','manifest')),
 storage_key text not null unique, filename text not null, sha256 text not null check(sha256 ~ '^[a-f0-9]{64}$'),
 size bigint not null check(size>0), unique(job_id,kind)
);
create table public.tx_events (
 id bigint generated always as identity primary key, job_id uuid not null references public.tx_jobs(id),
 stage text not null, elapsed_seconds double precision not null default 0 check(elapsed_seconds>=0), created timestamptz not null default now()
);
create table public.tx_reviews (
 id uuid primary key default gen_random_uuid(), job_id uuid not null references public.tx_jobs(id),
 reviewer uuid not null references public.tx_profiles(id), output_sha256 text not null,
 decision text not null check(decision in ('requires_revision','reviewed')),
 note text not null check(length(note) between 3 and 4000), created timestamptz not null default now()
);
create or replace function public.tx_can_read(p_owner uuid,p_baseline boolean) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from public.tx_profiles p where p.id=auth.uid() and p.active
 and (p.id=p_owner or p.role='admin' or (p_baseline and p.role in ('mentor','viewer'))));
$$;
alter table public.tx_profiles enable row level security;
alter table public.tx_workers enable row level security;
alter table public.tx_jobs enable row level security;
alter table public.tx_artifacts enable row level security;
alter table public.tx_events enable row level security;
alter table public.tx_reviews enable row level security;
create policy profiles_read_self on public.tx_profiles for select to authenticated using(id=auth.uid());
create policy jobs_read_allowed on public.tx_jobs for select to authenticated using(public.tx_can_read(owner,baseline));
create policy artifacts_read_allowed on public.tx_artifacts for select to authenticated using(kind<>'manifest' and exists(select 1 from public.tx_jobs j where j.id=job_id and public.tx_can_read(j.owner,j.baseline)));
create policy events_read_allowed on public.tx_events for select to authenticated using(exists(select 1 from public.tx_jobs j where j.id=job_id and public.tx_can_read(j.owner,j.baseline)));
create policy reviews_read_allowed on public.tx_reviews for select to authenticated using(exists(select 1 from public.tx_jobs j where j.id=job_id and public.tx_can_read(j.owner,j.baseline)));
-- Browser accounts receive no direct mutation grants. Only the authenticated API writes.
revoke all on public.tx_profiles,public.tx_workers,public.tx_jobs,public.tx_artifacts,public.tx_events,public.tx_reviews from anon,authenticated;
grant select on public.tx_profiles,public.tx_jobs,public.tx_artifacts,public.tx_events,public.tx_reviews to authenticated;
grant all on public.tx_profiles,public.tx_workers,public.tx_jobs,public.tx_artifacts,public.tx_events,public.tx_reviews to service_role;
grant usage,select on sequence public.tx_events_id_seq to service_role;
revoke all on function public.tx_can_read(uuid,boolean) from public;
grant execute on function public.tx_can_read(uuid,boolean) to authenticated;

create function public.tx_claim(p_worker uuid) returns setof public.tx_jobs
language plpgsql security definer set search_path='' as $$
declare picked uuid;
begin
 perform 1 from public.tx_workers where id=p_worker and enabled for update;
 if not found then return; end if;
 -- No automatic reclaim: an expired job may still be consuming GPU resources.
 if exists(select 1 from public.tx_jobs where worker_id=p_worker and status='running') then return; end if;
 select j.id into picked from public.tx_jobs j join public.tx_workers w on w.id=p_worker
 where j.status='queued' and (j.owner=w.owner or exists(select 1 from public.tx_profiles p where p.id=w.owner and p.active and p.role='admin'))
 order by j.created limit 1 for update of j skip locked;
 if picked is null then return; end if;
 return query update public.tx_jobs set status='running',stage='starting',worker_id=p_worker,
 lease=gen_random_uuid(),heartbeat=now(),updated=now() where id=picked returning *;
end; $$;

create function public.tx_finish(p_job uuid,p_worker uuid,p_lease uuid,p_artifact jsonb,p_metrics jsonb) returns void
language plpgsql security definer set search_path='' as $$
begin
 perform 1 from public.tx_jobs where id=p_job and worker_id=p_worker and lease=p_lease and status='running' for update;
 if not found then raise exception 'LEASE_INVALID'; end if;
 insert into public.tx_artifacts(job_id,kind,storage_key,filename,sha256,size)
 values(p_job,'result',p_artifact->>'storage_key',p_artifact->>'filename',p_artifact->>'sha256',(p_artifact->>'size')::bigint);
 update public.tx_jobs set status='succeeded',stage='complete',quality='requires_review',metrics=p_metrics,updated=now() where id=p_job;
 insert into public.tx_events(job_id,stage) values(p_job,'complete');
end; $$;
revoke all on function public.tx_claim(uuid),public.tx_finish(uuid,uuid,uuid,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.tx_claim(uuid),public.tx_finish(uuid,uuid,uuid,jsonb,jsonb) to service_role;

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('transformx-private','transformx-private',false,50000000,array['application/pdf','application/json'])
on conflict(id) do update set public=false,file_size_limit=50000000,allowed_mime_types=excluded.allowed_mime_types;
-- No browser storage policies: API authorizes each download and issues short-lived URLs.
commit;
