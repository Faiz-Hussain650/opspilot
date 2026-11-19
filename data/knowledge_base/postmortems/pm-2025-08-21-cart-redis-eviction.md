---
title: "PM-2025-08-21: Customers losing cart items (Redis eviction)"
service: cart-service
doc_type: postmortem
severity: sev2
tags: [redis, eviction, ttl, allkeys-lru, cart]
---

# PM-2025-08-21: Customers losing cart items (Redis eviction)

**Duration:** ~3 hours of degraded experience · **Severity:** sev2 · **Impact:** an estimated 15,000 carts lost some or all items; 640 support tickets.

## Summary

`cart-service` v2.9.0 introduced a "recently viewed" feature that wrote `cart:tmp:<session>`
keys **without a TTL**. Over three days these keys grew to 4.5 GB of the 6 GB `cart-cache`
Redis instance. To stop write errors, an engineer had earlier switched the eviction policy
from `volatile-lru` to `allkeys-lru` on a different incident ticket, so instead of failing
loudly Redis silently evicted real carts (which have TTLs) *and* the tmp keys.

## Detection

Not by monitoring - by customer support, after a spike of "my cart is empty" tickets. The
`redis_evicted_keys_total` metric was rising for two days but had no alert.

## Resolution

1. Rolled back `cart-service` to v2.8.4 to stop creating TTL-less keys.
2. Removed `cart:tmp:*` keys in batches with `SCAN` + `UNLINK`.
3. Set the eviction policy back to `volatile-lru`.

## Lessons

- Changing a Redis eviction policy is a behaviour change, not a tuning knob. It needs review.
- Every key we write must have a TTL; this is now enforced in the shared cache client library.

## Action items

1. Alert when `redis_evicted_keys_total` rate > 0 for 10 minutes on `cart-cache`. (done)
2. Add `maxmemory-policy` to the config drift check. (done)
3. Shared cache client rejects writes without TTL. (done)
