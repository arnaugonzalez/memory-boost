# Log

## [2026-03-12] claude-code | legacy-dashboard | Bumped jQuery from 1.12 to 3.7
- Two charts stopped rendering; reverted one of them.
- Pending: the rewrite.

## [2026-03-14] claude-code | legacy-dashboard | Pinned Node 14 in .nvmrc
- The build only works on Node 14. Nobody knows why.

## [2026-07-10] claude-code | pixel-notes | Offline queue for note edits
- Edits made offline are queued in SQLite and replayed on reconnect.

## [2026-07-15] codex | pixel-notes | Fixed duplicated notes after flaky sync
- Retried jobs were creating a note each time; added a stable job key.

## [2026-07-22] claude-code | pixel-notes | Background sync every 15 minutes
- Uses the OS background task API; iOS decides when "15 minutes" is.

## [2026-07-29] claude-code | pixel-notes | Conflict banner when two devices edit a note

## [2026-08-05] codex | pixel-notes | Reverted the conflict banner
- It appeared on every note opened on two devices, conflict or not.

## [2026-08-12] claude-code | pixel-notes | Per-field merge prototype
- Pending: decide between per-field merge and CRDT.

## [2026-08-20] claude-code | pixel-notes | Sync server moved to its own container

## [2026-08-28] claude-code | acme-api | Added idempotency keys to POST /invoices
- Keys stored 24h in the `idempotency` table.

## [2026-08-30] codex | pixel-notes | Fixed sync conflict on offline edits
- Last-writer-wins replaced by per-field merge.
- Pending: test on Android 15.

## [2026-09-01] claude-code | acme-api | Moved PDF rendering to the job queue
- Pending: load test with 10k invoices.
