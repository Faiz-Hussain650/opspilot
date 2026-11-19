---
title: "INC-1042: checkout 502s during Black Friday warm-up"
service: checkout-service
doc_type: ticket
severity: sev2
tags: [502, pgbouncer, orders_archive]
---

# INC-1042: checkout 502s during Black Friday warm-up

**Status:** Resolved · **Opened:** 2025-11-21 10:14 UTC · **Resolved:** 2025-11-21 10:41 UTC

## Description

Error rate on `POST /api/checkout` climbed to 6%. Logs full of `ERR_POOL_TIMEOUT`.

## Investigation

- No deploys in the last 24h.
- `pg_stat_activity` showed the `orders_archive` cron job running a 9 minute
  `DELETE FROM orders_history WHERE ...` that held locks; checkout queries queued behind it.
- PgBouncer `cl_waiting` was ~300.

## Resolution

Cancelled the archive query with `pg_cancel_backend`, suspended the `orders-archive` CronJob
until after the sale. Errors cleared within 3 minutes.

(Note from support: an engineer pasted `password=pgb-temp-7781` for the PgBouncer admin user
in this ticket during the incident. That credential has since been rotated.)

## Follow-up

Moved `orders_archive` schedule from 10:00 to 03:00 UTC and added batching (10k rows per transaction).
