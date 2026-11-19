---
title: Postgres primary running out of disk space
service: database
doc_type: runbook
severity: sev1
tags: [postgres, disk, wal, replication-slot, vacuum]
owner: data platform team
---

# Postgres primary running out of disk space

## Symptoms

- Alert `PostgresDiskUsageHigh` (80%) or `PostgresDiskUsageCritical` (90%) on `pg-main-0`.
- At 100% Postgres stops accepting writes: `PANIC: could not write to file "pg_wal/...": No space left on device`.
- Every service that writes to the main database starts failing - checkout, orders, accounts.

## Most likely causes (check in this order)

1. **An inactive replication slot is retaining WAL.** A replica or CDC connector (Debezium)
   was removed or is stuck, and its slot keeps WAL files forever.
2. **Table bloat** after a large delete, with autovacuum not keeping up.
3. **A huge temporary file** from a bad analytics query.

## Diagnosis

```sql
-- WAL retained by each slot
SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
FROM pg_replication_slots ORDER BY 3 DESC;

-- Biggest tables including indexes
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;
```

## Mitigation

### Inactive replication slot

If a slot is `active = false` and retaining tens of GB, and you have confirmed with the data
platform team that its consumer is gone, drop it:

```sql
SELECT pg_drop_replication_slot('debezium_orders');
```

WAL is recycled at the next checkpoint. Trigger one with `CHECKPOINT;`.

### Buy time

Expand the volume (online, no restart needed on our storage class):

```bash
kubectl -n data patch pvc data-pg-main-0 -p '{"spec":{"resources":{"requests":{"storage":"1500Gi"}}}}'
```

### Do not

- Never delete files from `pg_wal` by hand. It corrupts the database.
- Do not run `VACUUM FULL` on a big table during an incident - it takes an exclusive lock
  and needs free space equal to the table size.

## Verify

- Disk usage below 75% and falling.
- Writes succeed: `SELECT txid_current();` returns without error.
