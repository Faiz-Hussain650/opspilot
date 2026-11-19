---
title: "PM-2025-06-02: Payment failures during PayFlow degradation"
service: payments-service
doc_type: postmortem
severity: sev1
tags: [payflow, cardbridge, failover, retries, psp]
---

# PM-2025-06-02: Payment failures during PayFlow degradation

**Duration:** 1 hour 12 minutes · **Severity:** sev1 · **Impact:** payment success rate fell to 61%; roughly 9,800 failed payment attempts, 212 customers charged twice (refunded within 24h).

## Summary

PayFlow, our primary card processor, had a partial outage in their EU region and started
timing out on about 40% of authorisation requests. Our failover to CardBridge was delayed
by 48 minutes because the on-call engineer was not sure who was allowed to flip the
`payments.processor.routing` flag. Meanwhile, a recent config change had raised the client
retry count from 1 to 3, which tripled the load on PayFlow and caused duplicate
authorisations for requests that had actually succeeded but timed out on our side.

## Timeline (UTC)

- 09:41 - `PaymentSuccessRateLow` fires. PayFlow status page still green.
- 09:55 - On-call confirms timeouts are upstream using the curl health check from the runbook.
- 10:05 - Discussion in #inc-payments about whether failover needs finance approval.
- 10:43 - Payments engineering manager approves; routing flag set to 100% CardBridge.
- 10:44 - Success rate recovers to 96% within two minutes.
- 10:53 - Incident resolved. PayFlow confirms their incident at 10:20.

## Root causes

1. Upstream degradation at PayFlow (out of our control).
2. Unclear ownership of the failover decision.
3. Retry count raised to 3 in config PR #2231, merged without payments team review.

## Action items

1. Runbook updated: **on-call may fail over to CardBridge without approval** if PayFlow errors exceed 20% for 5 minutes. (done)
2. Retry count reverted to 1 and protected by CODEOWNERS. (done)
3. Idempotency key window extended from 1h to 24h to prevent double charges. (done)
4. Automate failover when the circuit breaker stays open for 5 minutes. (planned)
