---
title: Redis out of memory and key eviction (cart cache)
service: cart-service
doc_type: runbook
severity: sev2
tags: [redis, oom, eviction, maxmemory, cache]
owner: storefront team
---

# Redis out of memory and key eviction (cart cache)

## Symptoms

- `cart-service` logs `OOM command not allowed when used memory > 'maxmemory'`.
- Customers report items disappearing from their cart.
- Grafana **Redis / cart-cache** shows `redis_memory_used_bytes` at `redis_memory_max_bytes` (6 GB)
  and `redis_evicted_keys_total` increasing.

## Background

The `cart-cache` Redis instance stores active carts with a 7 day TTL. It runs with
`maxmemory 6gb`. The policy **must** be `volatile-lru` so only keys with a TTL are evicted.
If someone changed it to `noeviction`, writes start failing with the OOM error above.
If it was changed to `allkeys-lru`, session and rate-limit keys without TTLs get evicted too.

## Diagnosis

```bash
kubectl -n storefront exec -it redis-cart-0 -- redis-cli INFO memory | grep -E "used_memory_human|maxmemory_human|maxmemory_policy"
kubectl -n storefront exec -it redis-cart-0 -- redis-cli INFO stats | grep evicted_keys
```

Find the biggest keys (safe to run, it uses SCAN):

```bash
kubectl -n storefront exec -it redis-cart-0 -- redis-cli --bigkeys
```

A common culprit is keys without a TTL created by a bad deploy. Check a sample:

```bash
kubectl -n storefront exec -it redis-cart-0 -- redis-cli --scan --pattern 'cart:*' | head -50 | \
  xargs -I{} redis-cli TTL {}
```

A TTL of `-1` means the key never expires.

## Mitigation

1. Make sure the eviction policy is correct:

   ```bash
   kubectl -n storefront exec -it redis-cart-0 -- redis-cli CONFIG SET maxmemory-policy volatile-lru
   ```

2. If keys without TTL are filling memory, roll back the last `cart-service` deploy
   (see the deploy rollback runbook) and expire the bad keys in batches:

   ```bash
   kubectl -n storefront exec -it redis-cart-0 -- redis-cli --scan --pattern 'cart:tmp:*' | \
     xargs -n 500 redis-cli UNLINK
   ```

   Use `UNLINK`, not `DEL` - `UNLINK` frees memory in a background thread and does not block Redis.

3. If you need headroom right now, raise maxmemory temporarily (the node has 8 GB):

   ```bash
   kubectl -n storefront exec -it redis-cart-0 -- redis-cli CONFIG SET maxmemory 7gb
   ```

   Never go above 7 GB - Redis needs memory for fork during BGSAVE.

## Verify

- `used_memory` below 85% of `maxmemory`.
- No new `OOM command not allowed` errors in `cart-service` logs for 10 minutes.

## Escalation

Storefront on-call owns this. If carts were lost, tell customer support so they can handle
"my cart is empty" tickets.
