---
title: Payments gateway timeouts to the card processor
service: payments-service
doc_type: runbook
severity: sev1
tags: [payflow, psp, circuit-breaker, timeout, 504]
owner: payments-and-checkout team
---

# Payments gateway timeouts to the card processor

## Symptoms

- `payments-service` logs `PSP_TIMEOUT upstream=payflow elapsed_ms=10000` or `upstream connect error`.
- Checkout shows "Payment could not be processed, please try again" and the edge returns **504**.
- Grafana **Payments / Upstreams** shows `payflow_request_duration_seconds` p99 at the 10s timeout and
  `payflow_errors_total{reason="timeout"}` rising.
- The circuit breaker state metric `payments_circuit_state{upstream="payflow"}` flips to `1` (open).

## Background

Nimbus Shop authorises cards through **PayFlow** (primary processor). **CardBridge** is the
secondary processor and can take up to 100% of traffic. Routing is controlled by the feature
flag `payments.processor.routing` in the flag service. The circuit breaker opens after 50%
of requests fail within a 30 second window and stays open for 60 seconds.

## Diagnosis

1. Check PayFlow's public status page (status.payflow.example) and the #vendor-payflow channel.
2. Confirm the problem is upstream and not our own egress:

   ```bash
   kubectl -n payments exec deploy/payments-service -- \
     curl -s -o /dev/null -w "%{http_code} %{time_total}\n" https://api.payflow.example/health
   ```

   A healthy response is `200` in well under a second. Timeouts here mean PayFlow or the
   network path is the problem.
3. Check whether only one region is affected via the `region` label on `payflow_errors_total`.

## Mitigation

### Fail over to CardBridge

If PayFlow error rate is above 20% for more than 5 minutes, shift traffic:

```bash
flagctl set payments.processor.routing '{"payflow": 0, "cardbridge": 100}' --reason "INC: PayFlow timeouts"
```

The change propagates to all pods within 30 seconds. Watch `cardbridge_requests_total` start climbing.

### Do NOT retry aggressively

Never raise the client retry count above 1 during an upstream incident - retries multiply load
on a struggling processor and can cause duplicate authorisations. Idempotency keys protect us
from double charges, but only within a 24 hour window.

### Fail back

Once PayFlow has been healthy for 30 minutes, move traffic back in steps of 25% every 10 minutes:

```bash
flagctl set payments.processor.routing '{"payflow": 25, "cardbridge": 75}' --reason "fail back step 1"
```

## Verify

- Payment success rate on **Payments / Overview** is back above 97%.
- `payments_circuit_state{upstream="payflow"}` is `0` (closed).

## Escalation

Payment failures are always **sev1** if success rate is below 90%. Page the payments on-call and
the incident commander rotation. Customer support must be told within 15 minutes.
