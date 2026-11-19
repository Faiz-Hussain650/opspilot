---
title: "PM-2025-10-09: Storefront down after the TLS certificate expired"
service: edge
doc_type: postmortem
severity: sev1
tags: [tls, cert-manager, dns-01, expired-certificate]
---

# PM-2025-10-09: Storefront down after the TLS certificate expired

**Duration:** 38 minutes · **Severity:** sev1 · **Impact:** shop.nimbus.example unreachable for all browsers; mobile apps failed all API calls.

## Summary

The certificate for `shop.nimbus.example` expired at 06:12 UTC. cert-manager had been failing
to renew it for 30 days because the DNS provider API token used by the DNS-01 solver was
revoked during an access review. The `CertificateExpiringSoon` alert did fire 14 days earlier,
but it was routed to a Slack channel nobody watches (#alerts-platform-lowprio).

## Timeline (UTC)

- 06:12 - Certificate expires. Synthetic checks fail.
- 06:14 - Page to platform on-call.
- 06:25 - On-call identifies the expired cert; `describe challenge` shows `401 Unauthorized` from the DNS API.
- 06:36 - Emergency certificate from the vault installed on the ingress.
- 06:50 - Service restored. New DNS token issued; cert-manager renews successfully at 07:30.

## Action items

1. `CertificateExpiringSoon` now pages during business hours instead of posting to Slack. (done)
2. DNS automation tokens are excluded from the automated access review and owned by the platform team. (done)
3. Emergency certificate procedure documented in the TLS runbook. (done)
