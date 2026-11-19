---
title: Checkout DB connection pool exhausted (ERR_POOL_TIMEOUT)
service: checkout-service
doc_type: runbook
severity: sev2
tags: [postgres, pgbouncer, connection-pool, 502]
owner: payments-and-checkout team
---

# Checkout DB connection pool exhausted (ERR_POOL_TIMEOUT)

## Symptoms

- `checkout-service` returns **HTTP 502/503** at the edge, usually on `POST /api/checkout` and `POST /api/cart/confirm`.
- Application logs contain `ERR_POOL_TIMEOUT: timed out waiting for a connection after 5000ms`.
- Grafana dashboard **Checkout / Database** shows `db_pool_in_use` flat at the maximum (40 per pod) and `db_pool_wait_seconds` climbing.
- PgBouncer `SHOW POOLS;` shows `cl_waiting` > 0 for the `checkout` database.

## Why it happens

Each checkout pod keeps a pool of 40 connections to PgBouncer, which multiplexes them onto
120 real Postgres connections (`default_pool_size = 120`). The pool runs dry when:

1. **Slow queries hold connections** - most often a missing index after a migration, or the nightly
   `orders_archive` job running into business hours.
2. **Too many pods** - the HPA scaled checkout past 12 replicas, so 12 x 40 = 480 client
   connections are competing for 120 server connections.
3. **Connection leaks** - a code path that opens a transaction and returns early without
   releasing it (see postmortem PM-2025-03-14).

## Diagnosis

1. Check how many pods are running and whether the HPA just scaled:

   ```bash
   kubectl -n checkout get hpa checkout-service
   kubectl -n checkout get pods -l app=checkout-service | wc -l
   ```

2. Look at PgBouncer pool state:

   ```bash
   kubectl -n data exec deploy/pgbouncer -- psql -p 6432 -U pgbouncer pgbouncer -c "SHOW POOLS;"
   ```

   High `cl_waiting` with `sv_active` at 120 means the server side is saturated.

3. Find long-running queries on the primary:

   ```sql
   SELECT pid, now() - query_start AS duration, state, left(query, 120)
   FROM pg_stat_activity
   WHERE datname = 'checkout' AND state <> 'idle'
   ORDER BY duration DESC
   LIMIT 20;
   ```

## Mitigation

### Kill runaway queries

Anything in `pg_stat_activity` running longer than 60 seconds on the checkout database is
safe to cancel:

```sql
SELECT pg_cancel_backend(pid)
FROM pg_stat_activity
WHERE datname = 'checkout' AND state = 'active' AND now() - query_start > interval '60 seconds';
```

Use `pg_terminate_backend` only if `pg_cancel_backend` does not free the connection within 30 seconds.

### Cap the replicas

If the HPA scaled past 12 pods, pin it back:

```bash
kubectl -n checkout patch hpa checkout-service -p '{"spec":{"maxReplicas":12}}'
```

### Restart PgBouncer as a last resort

A rolling restart drops all client connections, so in-flight checkouts will fail once:

```bash
kubectl -n data rollout restart deploy/pgbouncer
kubectl -n data rollout status deploy/pgbouncer
```

### Pause the archive job

If `orders_archive` is running, suspend it until after peak hours:

```bash
kubectl -n data patch cronjob orders-archive -p '{"spec":{"suspend":true}}'
```

## Verify

- `db_pool_wait_seconds` p95 drops below 50ms within 5 minutes.
- 5xx rate on the **Checkout / Edge** dashboard is back under 0.5%.
- `SHOW POOLS;` shows `cl_waiting = 0`.

## Escalation

If the pool is still exhausted after the steps above, page the **database on-call**
(PagerDuty service `nimbus-data-platform`) and open a sev2 in #inc-checkout.
