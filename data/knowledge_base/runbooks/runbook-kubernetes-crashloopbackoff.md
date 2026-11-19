---
title: Pods in CrashLoopBackOff or OOMKilled
service: platform
doc_type: runbook
severity: sev3
tags: [kubernetes, crashloopbackoff, oomkilled, pods, probes]
owner: platform team
---

# Pods in CrashLoopBackOff or OOMKilled

## Symptoms

- `kubectl get pods` shows `CrashLoopBackOff`, `Error` or a high and increasing `RESTARTS` count.
- Alert `KubePodCrashLooping` fires in #alerts-platform.
- The service may be partially degraded if enough replicas are down.

## Diagnosis

Work through these in order - they cover 90% of cases.

1. **Why did the last container die?**

   ```bash
   kubectl -n <namespace> describe pod <pod>
   ```

   Look at `Last State: Terminated` and its `Reason`:
   - `OOMKilled` (exit code 137): the container went over its memory limit.
   - `Error` with exit code 1: the application crashed - read the logs.
   - `Completed` but restarting: the process exited normally - usually a wrong command/entrypoint.

2. **Read the logs of the crashed container**, not the new one:

   ```bash
   kubectl -n <namespace> logs <pod> --previous
   ```

   Typical findings: missing environment variable or secret, cannot connect to a dependency
   at startup, bad config after a deploy.

3. **Check the probes.** A liveness probe that is too aggressive will kill a slow-starting app.
   If `describe` shows `Liveness probe failed` events before the restart, the app may be healthy
   but slow. Java and ML services that load models at startup need a `startupProbe`.

4. **Did this start with a deploy?**

   ```bash
   kubectl -n <namespace> rollout history deploy/<name>
   ```

## Mitigation

- **Started after a deploy:** roll back first, debug later (see the deploy rollback runbook).
- **OOMKilled:** raise the memory limit by 50% as a stopgap and open a ticket for the owning team
  to find the leak or the bigger model/cache:

  ```bash
  kubectl -n <namespace> set resources deploy/<name> --limits=memory=1536Mi
  ```

- **Missing secret or config:** check the ExternalSecret synced:

  ```bash
  kubectl -n <namespace> get externalsecret
  ```

- **Probe too aggressive:** increase `initialDelaySeconds` or add a `startupProbe` with
  `failureThreshold: 30` and `periodSeconds: 10`.

## Verify

- `kubectl get pods` shows all replicas `Running` and `READY 1/1` with restarts no longer increasing.
- The `KubePodCrashLooping` alert resolves.
