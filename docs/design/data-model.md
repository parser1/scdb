# Control-plane data model

SQLite file `var/control.db`, created by migrations in `api/store`. Azure remains the source of truth for real resources. This database is the source of truth for intent, progress, and audit.

## Cluster status

`pending` → `provisioning` → `ready`

From `ready`: `degraded` while automation is frozen or a node is down, `restoring` during restore, `deleting` during delete, then `deleted`.

`error` is reachable from `provisioning`, `deleting`, `restoring`, and a failed replace. `error` keeps the last `step` and `error_message` so the UI can show where it stopped. A failed create can be deleted. It cannot be resumed except by the worker's own retry of an idempotent step still in `running`.

## Tables

### clusters

| Column | Notes |
| --- | --- |
| id | `c_` + ULID |
| name | Unique among non-deleted clusters. Used as the delete confirmation string |
| status | State machine above |
| location | Azure region |
| compute_rg, backup_rg | Resource group names |
| manager_install | `docker` or `package` |
| io_profile | `standard` or `demo` |
| cql_user, cql_secret | `demo_app` password. UI shows it once on the detail page |
| manager_secret | `scylla_manager` password |
| created_at, updated_at | UTC |
| layout_json | Subnet, identity, and storage ids the worker needs when it resumes a step |
| grafana_url, grafana_password | Ops VM link and Grafana admin password |
| backup_retain_until | When a deleted cluster's backup group may be removed |
| repair_task_id, backup_schedule_id | Manager schedule ids |
| storage_account, manager_agent_token | Backup account name and the agent token shared with Manager |

### nodes

| Column | Notes |
| --- | --- |
| id | `n_` + ULID |
| cluster_id | |
| ordinal | 0, 1, 2 for data; `-1` for ops |
| role | `data` or `ops` |
| vm_name, nic_name, data_disk_name | |
| private_ip | Stable |
| public_ip | Nullable. Node 0 and ops only |
| host_id | Scylla host id, captured after first boot. Required for replace |
| status | `provisioning`, `up`, `down`, `restarting`, `replacing` |

### operations

| Column | Notes |
| --- | --- |
| id | `op_` + ULID |
| cluster_id | Nullable only for future global tasks |
| type | `create_cluster`, `delete_cluster`, `restart_node`, `replace_node`, `backup`, `restore` |
| status | `queued`, `running`, `succeeded`, `failed` |
| step | Current step name from the workflow docs |
| error_message | |
| request_json | Sanitized. Secrets redacted |
| created_at, updated_at | |

### backups

| Column | Notes |
| --- | --- |
| id | `b_` + ULID |
| cluster_id | |
| operation_id | |
| manager_task_id | From Manager, when known |
| location | `azure:<container>` |
| status | `running`, `succeeded`, `failed` |
| started_at, finished_at | |

### audit_events

Append-only. No update method in the store.

| Column | Notes |
| --- | --- |
| id | integer primary key |
| at | UTC |
| actor | `operator` or `worker` |
| action | Verb, for example `cluster.create`, `node.replace`, `backup.finish` |
| cluster_id | |
| target | Node id, backup id, or empty |
| outcome | `accepted`, `succeeded`, `failed`, `blocked` |
| severity | `info` or `critical` |
| detail_json | Redacted |

## Invariants

- At most one cluster where status is not `deleted`.
- At most one `running` operation per cluster.
- A `replace_node` operation is rejected while any other data node has status other than `up`.
- `host_id` is written before the cluster is marked `ready`. Replace cannot run without it.
- Audit insert commits in the same transaction as the state change it describes.
