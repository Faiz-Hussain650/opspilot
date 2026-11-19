---
title: "INC-1150: inventory stuck because of a poison message on partition 7"
service: inventory-service
doc_type: ticket
severity: sev2
tags: [kafka, poison-message, dlq, consumer-lag]
---

# INC-1150: inventory stuck because of a poison message on partition 7

**Status:** Resolved · **Opened:** 2026-02-17 · **Resolved:** 2026-02-17

## Description

`KafkaConsumerLagHigh` for `inventory-service`. Lag of 180k on partition 7 of `order-events`,
all other partitions at 0. Several products oversold.

## Investigation

Consumer logs showed `DeserializationException` at the same offset every few seconds. A new
mobile app version sent an order event with `quantity` as a string ("2") instead of an integer.

## Resolution

Skipped the offset with the admin `/admin/skip` endpoint (event went to `order-events.dlq`),
lag drained in 6 minutes. Replayed the event from the DLQ after the consumer was patched to
coerce numeric strings.

## Follow-up

Schema validation added at the API gateway for order events.
