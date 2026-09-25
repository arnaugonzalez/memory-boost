---
title: Store money as integer minor units, never float
created: 2026-01-12
applies_when: [payments, invoices, postgres, typescript]
learned_on: acme-api
---
# Store money as integer minor units, never float

Sum 0.1 + 0.2 in a float column and an invoice is off by a cent. Store amounts as integers in
the smallest unit (`amount_cents BIGINT`) and format at the edge. Decimal types work too but the
integer rule survives every language and serializer.

## When it bit us

- acme-api, 2026-01-12: rounding drift on invoice totals; fixed by migrating to `amount_cents`.
