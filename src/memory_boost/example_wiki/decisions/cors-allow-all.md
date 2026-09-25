---
title: Allow all CORS origins in staging, for now
created: 2026-04-01
tags: [acme-api, infra]
---
# Allow all CORS origins in staging, for now

The mobile preview builds use random hostnames. `allow_origins=["*"]` in staging until we have a
fixed preview domain. Temporary.
