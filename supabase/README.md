# Hosted database

`migrations/` contains the hosted PostgreSQL schema, private worker assignment and
access-control functions. Supabase also supplies Auth and private Storage. Apply
migrations deliberately to the intended project; Git presence does not prove
they have been applied to a live database.

For administrator setup, copy `bootstrap-admin.example.sql` to ignored
`bootstrap-admin.sql`, replace the placeholder with the intended invited Auth
user, review the SQL, then run it manually. Keep project-specific identities and
server secrets out of shared configuration.

See [cloud setup](../docs/CLOUD_SETUP.md) and
[private workers](../docs/PRIVATE_GPU_WORKERS.md). The local FastAPI database is a
separate route described in `backend/config.py`.
