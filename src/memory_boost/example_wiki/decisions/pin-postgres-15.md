---
title: Pin PostgreSQL 15 until the pgvector upgrade is tested
created: 2026-02-20
review_by: 2026-06-01
tags: [acme-api, postgres, infra]
---
# Pin PostgreSQL 15 until the pgvector upgrade is tested

PostgreSQL 16 changed the default collation provider; our ORDER BY tests broke. Pin 15 in the
compose file and revisit once the pgvector image for 16 is tested in staging.

## Revisit

- Review by 2026-06-01: is the 16 image tested? If yes, unpin and delete this decision.
