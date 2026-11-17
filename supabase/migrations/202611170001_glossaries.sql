-- Apply once after 202611120001_job_recovery.sql. Adds customer glossaries; nothing existing changes.
-- One glossary per account and language pair: the account's required translations for its own terms
-- (product names, legal and department terms). The API attaches the matching one to each new job.
begin;
create table if not exists public.tx_glossaries (
 id uuid primary key default gen_random_uuid(),
 owner uuid not null references public.tx_profiles(id) on delete cascade,
 source_language text not null check (source_language ~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'),
 target_language text not null check (target_language ~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'),
 -- [{"source": "...", "target": "..."}], at most 100 entries (the engine's terminology limit).
 entries jsonb not null default '[]' check (jsonb_typeof(entries) = 'array' and jsonb_array_length(entries) <= 100),
 updated timestamptz not null default now(),
 unique (owner, source_language, target_language)
);
alter table public.tx_glossaries enable row level security;
-- Like the other tables, browsers get no direct access; only the authenticated API reads and writes.
revoke all on public.tx_glossaries from anon, authenticated;
grant all on public.tx_glossaries to service_role;
commit;
