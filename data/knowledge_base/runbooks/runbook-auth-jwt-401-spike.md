---
title: Spike of 401 Unauthorized from auth-service (JWT validation failures)
service: auth-service
doc_type: runbook
severity: sev2
tags: [jwt, jwks, 401, key-rotation, clock-skew, ntp]
owner: identity team
---

# Spike of 401 Unauthorized from auth-service (JWT validation failures)

## Symptoms

- Sudden rise of **HTTP 401** across many services at once; users get logged out.
- Logs show `JWT_INVALID_SIGNATURE kid=<key id> not found in JWKS` or
  `JWT_NOT_YET_VALID nbf=<timestamp>` / `JWT_EXPIRED`.
- Grafana **Auth / Tokens** shows `jwt_validation_failures_total` by `reason`.

## The three usual causes

| Log reason | Cause |
|---|---|
| `kid not found in JWKS` | Signing key was rotated but services still have the old JWKS cached |
| `JWT_NOT_YET_VALID` / `JWT_EXPIRED` on fresh tokens | Clock skew on one or more nodes (NTP broken) |
| `JWT_INVALID_AUDIENCE` | A service was deployed with the wrong `AUTH_AUDIENCE` config |

## Diagnosis

1. Group failures by reason:

   ```bash
   kubectl -n identity logs deploy/auth-service --since=10m | grep JWT_ | awk '{print $5}' | sort | uniq -c
   ```

2. Check if a key rotation happened recently - the rotation job posts to #identity-changes,
   and the JWKS endpoint lists active keys:

   ```bash
   curl -s https://auth.nimbus.internal/.well-known/jwks.json | jq '.keys[].kid'
   ```

3. Check clock skew on the nodes running the failing pods:

   ```bash
   kubectl get pods -A -o wide | grep <failing-service>   # find the node
   kubectl debug node/<node> -it --image=busybox -- chronyc tracking
   ```

   A `System time` offset greater than 2 seconds is a problem; our token leeway is 30 seconds,
   so failures start around 30s of skew.

## Mitigation

### After a key rotation

Services cache the JWKS for 10 minutes. Force a refresh by restarting the affected
deployments, or wait out the TTL if the impact is small:

```bash
kubectl -n <namespace> rollout restart deploy/<service>
```

The rotation procedure should publish the new key to JWKS **24 hours before** signing with it.
If that overlap was skipped, re-add the previous key to the JWKS as a stopgap:

```bash
authctl keys publish --kid <previous-kid> --until "+24h"
```

### Clock skew

Cordon and drain the skewed node, then fix chrony:

```bash
kubectl cordon <node> && kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
```

## Verify

- `jwt_validation_failures_total` returns to its baseline (under 5/min).
- Login success rate on **Auth / Overview** above 99%.
