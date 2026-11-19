---
title: Rolling back a bad deploy
service: platform
doc_type: runbook
severity: sev3
tags: [deploy, rollback, argo-rollouts, kubectl, canary]
owner: platform team
---

# Rolling back a bad deploy

## When to roll back

Roll back **first** and investigate later if any of these started within 30 minutes of a deploy:

- error rate up more than 2x on the service that was deployed,
- latency p99 up more than 50%,
- pods crash looping,
- a downstream service started failing right after.

"It might be a coincidence" is not a reason to wait. Rolling back is cheap; a long outage is not.

## How deploys work here

Services are deployed by Argo CD from the `nimbus-deploy` repo. Customer-facing services
(checkout, payments, cart, auth) use **Argo Rollouts** with a canary: 10% -> 50% -> 100%,
5 minutes per step, with automatic analysis on error rate. Internal services use a plain
Kubernetes `Deployment`.

## Rollback steps

### Service using Argo Rollouts

If the canary is still in progress, abort it - traffic goes back to the stable version immediately:

```bash
kubectl argo rollouts abort <service> -n <namespace>
```

If the rollout already completed, undo to the previous revision:

```bash
kubectl argo rollouts undo <service> -n <namespace>
kubectl argo rollouts get rollout <service> -n <namespace> --watch
```

### Plain Deployment

```bash
kubectl -n <namespace> rollout undo deploy/<service>
kubectl -n <namespace> rollout status deploy/<service>
```

### Stop Argo CD from re-applying the bad version

Argo CD will sync the bad version back from git unless you pause auto-sync:

```bash
argocd app set <service> --sync-policy none
```

Then revert the commit in `nimbus-deploy` and re-enable auto-sync once the revert is merged.

## Database migrations

If the bad deploy ran a migration, **do not** roll back the schema in a panic. Our migrations
are expand/contract, so the previous app version works with the new schema. Check the
migration notes in the PR before touching the database.

## Verify

- Error rate and latency back to the pre-deploy baseline on the service dashboard.
- Post in #deploys that the version was rolled back and why, and tag the author.
