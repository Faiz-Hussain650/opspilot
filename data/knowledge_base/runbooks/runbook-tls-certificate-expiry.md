---
title: TLS certificate expiring or expired (cert-manager)
service: edge
doc_type: runbook
severity: sev1
tags: [tls, certificate, cert-manager, ingress, letsencrypt]
owner: platform team
---

# TLS certificate expiring or expired (cert-manager)

## Symptoms

- Browsers show `NET::ERR_CERT_DATE_INVALID`; API clients fail with
  `x509: certificate has expired or is not yet valid`.
- Alert `CertificateExpiringSoon` (fires at 14 days) or `CertificateNotReady`.
- Synthetic checks for `shop.nimbus.example` fail on the TLS handshake.

## Background

Public certificates are issued by Let's Encrypt via **cert-manager** using the `ClusterIssuer`
named `letsencrypt-prod` with the DNS-01 solver. Certificates renew automatically 30 days before
expiry. If renewal fails for 16 days in a row we get the 14-day alert - so when it fires,
something has already been broken for a while.

## Diagnosis

```bash
kubectl get certificates -A
kubectl -n edge describe certificate shop-nimbus-tls
kubectl -n edge get certificaterequests,orders,challenges
```

The most common failure: the DNS-01 challenge cannot create the TXT record because the
DNS provider API token expired or lost permissions. Look for this in the challenge events:

```bash
kubectl -n edge describe challenge <name> | grep -A5 Events
kubectl -n cert-manager logs deploy/cert-manager --since=1h | grep -i error
```

Check the expiry date actually served:

```bash
echo | openssl s_client -connect shop.nimbus.example:443 -servername shop.nimbus.example 2>/dev/null | openssl x509 -noout -enddate
```

## Mitigation

1. Fix the root cause (usually rotate the DNS API token in the secrets manager; the
   ExternalSecret `dns-provider-token` syncs it into the `cert-manager` namespace).
2. Force a renewal:

   ```bash
   kubectl cert-manager renew -n edge shop-nimbus-tls
   ```

   or, without the plugin, delete the secret so cert-manager re-issues it:

   ```bash
   kubectl -n edge delete secret shop-nimbus-tls
   ```

3. If the certificate has **already expired** and issuance is still failing, install the
   emergency backup certificate (valid 90 days, stored in the vault at
   `platform/tls/emergency-shop`) as the ingress TLS secret and keep working on cert-manager.

## Verify

- `kubectl get certificate -n edge shop-nimbus-tls` shows `READY=True`.
- The `openssl` command above shows a `notAfter` date roughly 90 days away.

## Escalation

An expired public certificate is a **sev1**: the whole storefront is down for every customer.
Page platform on-call and the incident commander immediately.
