# Backup and restore

Scylla Manager owns the data path. The control plane starts tasks, stores their ids, and surfaces status.

## Location

Container `scylla-backup` on the per-cluster storage account. Manager location string `azure:scylla-backup`. Agents authenticate with the VM managed identity. The account name is in the agent config. This matches Manager's Azure Blob setup: IAM on each node, `azure.account` set, `check-location` clean.

Schema backup requires the CQL role, which provisioning creates before the first schedule.

## Schedules created at the end of provisioning

| Task | Policy |
| --- | --- |
| Repair | Weekly, all keyspaces, Manager defaults for intensity |
| Backup | Daily, location `azure:scylla-backup`, retention 7 days |

Retention of 7 matches the backup resource group's keep window after cluster delete. A backup object and the Azure container are the same generation of data; we do not track a second retention number.

## On-demand backup

`POST /api/v1/clusters/{id}/backups` queues operation `backup`.

| Step | Action |
| --- | --- |
| `start` | Ask Manager to start the backup task now |
| `poll` | Poll Manager until the task run succeeds or fails. UI shows the Manager progress string in `step` |
| `record` | Insert `backups` row `succeeded` or `failed`, audit `backup.finish` |

Down nodes are skipped by Manager itself. If a node is down, the operation still runs, and the audit detail records that Manager reported a partial node set. The UI shows that sentence. We do not silently call a partial backup complete without that note.

## Restore

`POST /api/v1/clusters/{id}/restores` with `backup_id` and `confirm` equal to the cluster name.

Preconditions: cluster `ready`, backup `succeeded`, no node `down`, confirmation matches.

| Step | Action |
| --- | --- |
| `quiesce` | Mark cluster `restoring` so the health loop will not replace nodes mid-restore |
| `restore` | `sctool restore` for that backup, restoring schema and data for keyspace `demo`. Other keyspaces are left alone if the operator created them by hand; the demo client only uses `demo` |
| `verify` | Run Command a `SELECT count(*)` on `demo.events` if the table exists. Store the count on the operation. This is a smoke check, not a consistency proof |
| `ready` | Cluster `ready`, audit `restore.finish` |

Failure sets cluster `error` only if Manager reports the restore failed. A failed smoke query sets the operation `failed` and the cluster back to `ready`, with the audit explaining that the restore command finished and the check did not. Those are different situations and the UI should not imply the data was rolled back.

## In-place restore is a demo choice

Production would restore into a new cluster and cut clients over. In-place restore can overwrite the `demo` keyspace the operator is looking at. The confirmation gate and the single-keyspace scope are the mitigations inside this homework. The limitation is repeated on the restore button as helper text.

## What the operator can prove in the demo

1. Client inserts a row.
2. On-demand backup succeeds and a blob prefix exists in the container.
3. Client inserts a second row, or deletes the first.
4. Restore.
5. Client reads and sees the backed-up row.

The CQL client gains a `--read` mode so this script does not require `cqlsh` on the laptop. `cqlsh` on node 0 remains available for the operator who wants it.
