---
title: Make background jobs idempotent before adding retries
created: 2026-08-30
applies_when: [background-jobs, offline-sync, queues]
learned_on: pixel-notes
---
# Make background jobs idempotent before adding retries

Retrying a non-idempotent job duplicated notes on every flaky sync. Give each job a stable key
(entity id + version) and make the handler a no-op when the key was already applied; only then
add retries with backoff. Tools that helped: a `--replay <job-id>` CLI flag and a test that runs
the same job twice and asserts one side effect.

## When it bit us

- pixel-notes, 2026-08-30: offline edits synced twice after a timeout, users saw duplicate notes.
