-- Copy to ignored bootstrap-admin.sql and replace this placeholder locally.
-- Run manually only AFTER creating/inviting the intended Auth user.
-- Explicit administrator approval, never signup metadata, grants this role.
insert into public.tx_profiles(id,role)
select id,'admin' from auth.users
where lower(email)='replace-me@example.invalid'
on conflict(id) do update set role='admin',active=true;

select id,role,active from public.tx_profiles where id in
(select id from auth.users where lower(email)='replace-me@example.invalid');
