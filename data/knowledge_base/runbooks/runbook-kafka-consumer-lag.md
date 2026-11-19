---
title: Kafka consumer lag on order-events (inventory not updating)
service: inventory-service
doc_type: runbook
severity: sev2
tags: [kafka, consumer-lag, order-events, poison-message, dlq]
owner: fulfilment team
---

# Kafka consumer lag on order-events (inventory not updating)

## Symptoms

- Alert `KafkaConsumerLagHigh` for consumer group `inventory-service` on topic `order-events`.
- Stock levels on the storefront are stale: items show "in stock" after they sold out.
- Grafana **Kafka / Consumers** shows `kafka_consumergroup_lag` growing on one or more partitions.

## Two very different causes

**Lag on ONE partition, others at zero** - almost always a **poison message**: an event the
consumer cannot process, so it retries forever and the partition stops moving.

**Lag on ALL partitions** - the consumer is too slow or down: not enough replicas, a slow
database, or the pods are crash looping.

## Diagnosis

```bash
kubectl -n kafka exec -it kafka-0 -- kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 --describe --group inventory-service
```

Look at the `LAG` column per partition and note the `CURRENT-OFFSET` of any stuck partition.

Check consumer logs for repeated failures at the same offset:

```bash
kubectl -n fulfilment logs deploy/inventory-service --since=15m | grep -E "offset=|DeserializationException|ProcessingFailed"
```

## Mitigation

### Poison message on one partition

1. Confirm the same offset fails repeatedly in the logs.
2. Send the message to the dead letter topic and skip it. The service has an admin endpoint for this:

   ```bash
   kubectl -n fulfilment port-forward deploy/inventory-service 8081:8081
   curl -X POST localhost:8081/admin/skip -d '{"topic":"order-events","partition":7,"offset":918273}'
   ```

   The skipped event lands on `order-events.dlq` so it can be replayed after a fix.
3. Never reset the whole consumer group offset to `latest` - you will silently lose every
   unprocessed order event.

### Lag on all partitions

1. Scale out, but never above the partition count (24), extra consumers sit idle:

   ```bash
   kubectl -n fulfilment scale deploy/inventory-service --replicas=12
   ```

2. Check the inventory database for slow queries (same approach as the checkout pool runbook).

## Verify

- Lag trends down on every partition and reaches zero.
- Spot check: a fresh test order decrements stock within 30 seconds.

## Escalation

Fulfilment on-call. If lag is above 500k messages, also inform the storefront team, because
overselling will start and orders may need to be cancelled.
