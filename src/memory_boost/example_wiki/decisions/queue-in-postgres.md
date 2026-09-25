---
title: Job queue in PostgreSQL
status: current
created: 2026-03-04
review_by: 2027-03-01
---
# Job queue in PostgreSQL

## Decision

acme-api keeps its job queue in PostgreSQL (`SKIP LOCKED`) instead of adding Redis.

## Why

One less service to run; throughput needed is below 50 jobs/s.
