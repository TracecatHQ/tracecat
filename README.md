# Tracecat

Tracecat is an open source Tines alternative.

## Quickstart

Run `docker compose up` to spin up Tracecat's automation engine and low-code frontend.

## Development

Tracecat uses NGINX as an API gateway to the `api` and `runner` services.
You can access these services respectively via the `/api/` and `/runner/` endpoints.

We use Supabase for auth and app (data models outside of the workflow engine's events / logs) storage.

```
SUPABASE_JWT_ALGORITHM=HS256
SUPABASE_JWT_SECRET=your-secret-here
SUPABASE_PSQL_URL=postgres://postgres.[your-user-name]:[your-password]@[your-supabase-region].pooler.supabase.com:5432/postgres
```

You will also need to generate a signing secret for the runner:

```
export TRACECAT__SIGNING_SECRET=${openssl rand -hex 32}
```
