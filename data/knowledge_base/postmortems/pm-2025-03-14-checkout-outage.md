---
title: "PM-2025-03-14: Checkout outage caused by a connection leak"
service: checkout-service
doc_type: postmortem
severity: sev1
tags: [postgres, connection-pool, leak, ERR_POOL_TIMEOUT]
---

# PM-2025-03-14: Checkout outage caused by a connection leak

**Duration:** 47 minutes (14:02 - 14:49 UTC) · **Severity:** sev1 · **Impact:** ~38% of checkout attempts failed with 502; an estimated 4,100 orders were lost or delayed.

## Summary

A change to the promo-code validation path in `checkout-service` v4.18.0 opened a database
transaction and, when the promo code was invalid, returned early without committing or
rolling back. The connection was never returned to the pool. Under normal traffic each pod
leaked a few connections per minute; at the 14:00 marketing email peak every pod's 40-connection
pool was exhausted within minutes, and requests failed with `ERR_POOL_TIMEOUT`.

## Timeline (UTC)

- 13:20 - v4.18.0 rolled out to 100% (canary analysis passed; the leak was too slow to show up in 15 minutes).
- 14:00 - marketing email sent; checkout traffic triples.
- 14:02 - `CheckoutErrorRateHigh` fires. On-call follows the connection pool runbook.
- 14:10 - On-call restarts PgBouncer. Errors drop for 4 minutes, then return. (Restarting PgBouncer did not help because the leak was in the app pods.)
- 14:21 - HPA scales checkout to 18 pods, making contention at PgBouncer worse.
- 14:33 - Incident commander notices the errors started after the deploy and calls for a rollback.
- 14:38 - `kubectl argo rollouts undo checkout-service` completes.
- 14:49 - Error rate back to baseline. Incident resolved.

## Root cause

Early `return` inside a `with db.transaction()` block that had been refactored to a manual
`begin()` without a `finally: release()`.

## What went well

- The runbook diagnosis steps quickly showed `cl_waiting` climbing at PgBouncer.

## What went wrong

- We spent 31 minutes treating the symptom (PgBouncer, pool size) before checking the deploy history.
- The HPA scaled up and made things worse - more pods means more client connections competing for the same 120 server connections.

## Action items

1. Add "check recent deploys, roll back first" as step 1 in the connection pool runbook. (done)
2. Cap checkout HPA `maxReplicas` at 12 until PgBouncer pool sizing is revisited. (done)
3. Add a lint rule forbidding manual `begin()` without `finally`. (done)
4. Add `db_pool_in_use` slope to canary analysis so slow leaks fail the canary. (in progress)
