---
title: "INC-1189: recommendations pods OOMKilled after model update"
service: recommendations-service
doc_type: ticket
severity: sev3
tags: [oomkilled, kubernetes, ml-model, memory-limit]
---

# INC-1189: recommendations pods OOMKilled after model update

**Status:** Resolved · **Opened:** 2026-04-03 · **Resolved:** 2026-04-03

## Description

All `recommendations-service` pods in `CrashLoopBackOff`. Product pages fell back to the
static "popular items" list.

## Investigation

`kubectl describe pod` showed `Last State: Terminated, Reason: OOMKilled, Exit Code: 137`.
The ML team had shipped a new embedding model that is 2.3x bigger than the previous one; the
memory limit was still 1Gi.

## Resolution

Raised the limit to 2560Mi with `kubectl set resources`, then made the change permanent in the
Helm values. Also added a `startupProbe` because the larger model takes ~70s to load and the
liveness probe was killing pods that were still starting.

## Follow-up

Model release checklist now includes a memory profile and a resource limit review.
