---
title: acme-api
created: 2026-01-10
updated: 2026-09-01
tags: [python, fastapi, postgres, background-jobs]
---
# acme-api

Invoice API for a fictional shop. FastAPI + PostgreSQL, deployed as a container.
Run locally with `make dev`; tests with `pytest -q`.

## Architecture

- `app/routes/` HTTP layer, `app/billing/` pure domain logic, `app/db/` repositories.
- Background jobs use a PostgreSQL table with `SELECT ... FOR UPDATE SKIP LOCKED`, not Redis.

## Gotchas

- The staging database is reset every Sunday: never keep fixtures there.
- Currency amounts are stored as integer cents; never read the legacy `amount_float` column.

## History

- 2026-02: migrated from Flask to FastAPI.
