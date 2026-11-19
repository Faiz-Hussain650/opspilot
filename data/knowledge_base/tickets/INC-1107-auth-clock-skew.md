---
title: "INC-1107: random logouts caused by clock skew on one node"
service: auth-service
doc_type: ticket
severity: sev3
tags: [jwt, clock-skew, chrony, ntp, 401]
---

# INC-1107: random logouts caused by clock skew on one node

**Status:** Resolved · **Opened:** 2026-01-08 · **Resolved:** 2026-01-08

## Description

About 8% of API requests to `orders-service` failed with 401 `JWT_NOT_YET_VALID`. Only some
users were affected and retrying usually worked.

## Investigation

The failing requests all hit pods on node `ip-10-4-21-87`. `chronyc tracking` on the node
showed the clock 47 seconds behind. The node's chrony could not reach the NTP servers
because a security group change blocked UDP 123 outbound.

## Resolution

Cordoned and drained the node, fixed the security group, uncordoned after chrony resynced.

## Follow-up

Added a node-exporter alert: `node_timex_offset_seconds` > 1s for 5 minutes.
