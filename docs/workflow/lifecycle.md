# Lifecycle

After `ready`, the operator's day-2 surface is small on purpose.

| Want | Action |
| --- | --- |
| See progress | Poll the operation. The UI does this itself |
| See nodes | `GET /clusters/{id}` includes node status, private IP, and the public CQL address of node 0 |
| Open dashboards | Link to Grafana on the ops public IP, operator CIDR only |
| Restart one Scylla process | `POST .../nodes/{nodeId}/restart` |
| Replace a VM | `POST .../nodes/{nodeId}/replacement` |
| Back up or restore | Workflow in [backup-and-restore.md](backup-and-restore.md) |
| Read what happened | Audit list |
| Remove the cluster | Delete below |

There is no stop-the-cluster-to-save-money action in v1. Deallocate would require a coordinated Scylla shutdown and a startup order on resume, which is a second lifecycle. The cost control is delete.

## Delete

`POST /api/v1/clusters/{id}/deletion` with `{"confirm": "<cluster name>", "delete_backups": false}`.

| Step | Action |
| --- | --- |
| `validate` | Confirmation matches, no other operation is `running`. A restore in progress blocks delete |
| `stop_schedules` | Disable Manager backup and repair tasks so they do not fire during teardown. Failure here is logged and does not block delete |
| `delete_vms` | Delete data VMs and ops VM. NICs and public IPs go with the compute group in the next step |
| `delete_compute_group` | Delete `rg-scylla-<id>` |
| `retain_backups` | If `delete_backups` is false, write `backup_retain_until = now + 7 days` and leave the backup RG. If true, delete the backup RG now |
| `deleted` | Cluster status `deleted`. The row stays so the audit history still joins |

A daily worker pass deletes backup resource groups whose `backup_retain_until` has passed. The pass is part of the health loop's slow tick.

## Read path for the operator

The cluster detail page shows status, the current operation step, three nodes, a Grafana URL, the CQL contact line for `demo_app`, the latest backup, and buttons for backup, restore, restart, replace, and delete. Buttons that the state machine would reject are disabled and the reason is the API error text, not a separate UI invention.

## Reconciliation

Not required for the first demo. Task T29 adds a read-only pass: list resource groups tagged `app=scylla-dbaas`, compare to SQLite, and show orphans. It does not delete anything it does not recognize.
